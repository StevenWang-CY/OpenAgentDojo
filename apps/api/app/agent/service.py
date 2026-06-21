"""Agent service — orchestrates intent classification, template rendering,
optional LLM narration, DB persistence, and patch application (plan §8).

Key invariants (M4 polish):

* The :class:`AnthropicClient` is constructed lazily — never at import time
  and never in :meth:`AgentService.__init__`. This keeps the API process
  bootable when ``civitas_core`` is absent (CI, dev laptops).
* Per-mission intent classifiers and template renderers are cached by
  ``(mission_id, manifest_sha256)`` so repeated turns don't re-parse YAML or
  Jinja templates.
* Every ``respond`` / ``apply_patch`` body runs inside ``async with
  db.begin()`` so the AgentTurn insert and supervision events emit
  atomically.
* In addition to ``agent.responded``, ``respond`` emits ``patch.proposed`` when
  the classified intent is ``"fix"`` and an agent patch file exists on disk.
* ``apply_patch`` emits ``patch.failed`` with the apply stderr when the
  sandbox driver reports failure.
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from functools import cached_property
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.intents import IntentClassifier
from app.agent.llm import AnthropicClient
from app.agent.templates import TemplateRenderer
from app.config import get_settings
from app.missions.resolver import MissionFolderNotFoundError, resolve_mission_dir
from app.models.agent_turn import AgentTurn
from app.models.session import SessionRow
from app.observability import agent_responses_total
from app.schemas.agent_turn import AgentTurnResponse, PatchResult
from app.schemas.session import ContextSelection
from app.sessions.events import EventEmitter


def _find_mission_folder(mission_id: str, missions_root: Path) -> Path | None:
    """Scan ``missions_root`` for the folder backing ``mission_id``.

    Missions follow the ``NN-id/`` naming convention; we delegate to the
    shared :func:`resolve_mission_dir` so the agent, submit, and reports
    surfaces all agree on which folder belongs to a given id (P0-B5).
    """
    try:
        return resolve_mission_dir(missions_root, mission_id)
    except (MissionFolderNotFoundError, ValueError) as exc:
        logger.debug("[agent] could not resolve mission folder for {}: {}", mission_id, exc)
        return None


def _context_to_list(context: ContextSelection) -> list[str]:
    """Flatten ContextSelection into a single list of strings (files first)."""
    items: list[str] = []
    items.extend(context.files)
    items.extend(context.logs)
    items.extend(context.tests)
    items.extend(context.extras)
    return items


def _manifest_sha(manifest: Any) -> str:
    """Return the manifest's content sha. Raises when the sha is missing.

    Mirrors the contract enforced in :func:`app.grading.runner._build_prompt_judgements`:
    silently falling back to ``manifest.id`` lets a stale (id-keyed)
    judgement cache survive an edit to the prompts / intents /
    forbidden_changes block, which violates the determinism contract
    that re-grading after a mission edit produces different numbers.
    Surfacing it loudly forces the bug to be fixed at the loader (where
    the sha is computed from the YAML bytes) rather than papered over
    here.
    """
    sha = getattr(manifest, "manifest_sha256", "") or ""
    if sha:
        return str(sha)
    # Some callers pass a LoadedMission; honour it transparently.
    inner = getattr(manifest, "manifest", None)
    if inner is not None:
        sha2 = getattr(inner, "manifest_sha256", "") or ""
        if sha2:
            return str(sha2)
    raise RuntimeError(
        f"manifest_sha256 missing for mission "
        f"{getattr(manifest, 'id', None) or getattr(inner, 'id', None) or '<unknown>'}"
    )


def _manifest_id(manifest: Any) -> str:
    inner = getattr(manifest, "manifest", manifest)
    return str(getattr(inner, "id", "") or "")


def _manifest_failure_mode(manifest: Any) -> tuple[str, str]:
    inner = getattr(manifest, "manifest", manifest)
    fm = getattr(inner, "failure_mode", None)
    if fm is None:
        return ("", "")
    return (
        str(getattr(fm, "title", "") or ""),
        str(getattr(fm, "description", "") or ""),
    )


def _manifest_patch_file(manifest: Any) -> str | None:
    """Return the manifest's agent patch filename, or None when unset.

    An empty string is treated as unset so callers that build a Path with it
    don't accidentally end up with a directory that ``exists()`` reports True.
    """
    inner = getattr(manifest, "manifest", manifest)
    agent_cfg = getattr(inner, "agent", None)
    raw = getattr(agent_cfg, "patch_file", "") or ""
    value = str(raw).strip()
    return value or None


def _manifest_banned_tokens(manifest: Any) -> tuple[str, ...]:
    """Optional banned-token list pulled from a manifest if it declares one."""
    inner = getattr(manifest, "manifest", manifest)
    agent_cfg = getattr(inner, "agent", None)
    tokens = getattr(agent_cfg, "banned_tokens", None) if agent_cfg else None
    if tokens is None:
        return ()
    if isinstance(tokens, str):
        return (tokens,)
    try:
        return tuple(str(t) for t in tokens if t)
    except TypeError:
        return ()


def _build_patch_failed_payload(
    *,
    turn_index: int,
    turn_id: uuid.UUID | str,
    error: str,
    file_count: int = 0,
    added: int = 0,
    removed: int = 0,
) -> dict[str, Any]:
    """Canonical ``patch.failed`` event payload (Phase 4.A.4).

    All three failure branches (timeout, generic exception, driver-reported
    ``result.applied=False``) MUST emit the same shape so subscribers
    (timeline renderer, scoring engine, post-mortem walkthrough) can read
    fields unconditionally. The contract is:

      * ``turn_index``  — int turn the patch belonged to
      * ``error``       — generic message safe to surface to the client
      * ``file_count``  — number of files the patch attempted to touch
                          (0 when unknown — timeout / exception paths)
      * ``added``       — added line count (0 when unknown)
      * ``removed``     — removed line count (0 when unknown)
      * ``turn_id``     — stringified turn UUID for direct DB join

    Mirrored in ``packages/shared-types/src/events.ts`` — keep that file
    in sync when touching this shape (Agent 4.B owns shared-types).
    """
    return {
        "turn_index": int(turn_index),
        "error": error,
        "file_count": int(file_count),
        "added": int(added),
        "removed": int(removed),
        "turn_id": str(turn_id),
    }


# ---------------------------------------------------------------------------
# Per-mission caches
# ---------------------------------------------------------------------------

_INTENT_CLASSIFIER_CACHE: dict[tuple[str, str], IntentClassifier] = {}
_TEMPLATE_RENDERER_CACHE: dict[tuple[str, str], TemplateRenderer] = {}
_CACHE_LOCK = threading.Lock()


def _classifier_for(manifest: Any, folder: Path) -> IntentClassifier:
    mission_id = _manifest_id(manifest)
    sha = _manifest_sha(manifest)
    cache_key = (mission_id, sha)
    with _CACHE_LOCK:
        cached = _INTENT_CLASSIFIER_CACHE.get(cache_key)
    if cached is not None:
        return cached
    classifier = IntentClassifier.for_mission(
        _MissionProxy(folder=folder, manifest=getattr(manifest, "manifest", manifest))
    )
    with _CACHE_LOCK:
        _INTENT_CLASSIFIER_CACHE[cache_key] = classifier
    return classifier


def _renderer_for(manifest: Any, folder: Path) -> TemplateRenderer:
    mission_id = _manifest_id(manifest)
    sha = _manifest_sha(manifest)
    cache_key = (mission_id, sha)
    with _CACHE_LOCK:
        cached = _TEMPLATE_RENDERER_CACHE.get(cache_key)
    if cached is not None:
        return cached
    renderer = TemplateRenderer(
        mission_id=mission_id or folder.name,
        manifest_sha=sha,
        mission_folder=folder,
    )
    with _CACHE_LOCK:
        _TEMPLATE_RENDERER_CACHE[cache_key] = renderer
    return renderer


def clear_agent_caches() -> None:
    """Drop classifier + template renderer caches. Intended for tests."""
    with _CACHE_LOCK:
        _INTENT_CLASSIFIER_CACHE.clear()
        _TEMPLATE_RENDERER_CACHE.clear()


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class AgentService:
    """Orchestrate the deterministic agent pipeline (plan §8).

    A single instance is created at module load time (see ``agent/router.py``)
    and shared across requests. State on the instance is limited to the lazy
    LLM client reference; per-mission caches live at module scope.
    """

    def __init__(
        self,
        llm: AnthropicClient | None = None,
        settings: Any | None = None,
    ):
        # Do NOT instantiate AnthropicClient here — defer to first use so the
        # process boots even when civitas_core is missing. ``llm`` can be
        # supplied for tests (typically a fake) and short-circuits the
        # cached_property.
        self._llm_override = llm
        self._settings = settings or get_settings()

    @cached_property
    def llm(self) -> AnthropicClient:
        """Lazily-constructed LLM adapter — first access only."""
        if self._llm_override is not None:
            return self._llm_override
        return AnthropicClient()

    # Convenience for tests that want to swap in a fake LLM mid-flight.
    def set_llm(self, llm: AnthropicClient) -> None:
        self._llm_override = llm
        # Invalidate the cached_property if it was already materialised.
        self.__dict__.pop("llm", None)

    # ------------------------------------------------------------------
    # respond
    # ------------------------------------------------------------------

    async def respond(
        self,
        db: AsyncSession,
        session: SessionRow,
        prompt: str,
        context: ContextSelection,
        mission_folder: Path,
        manifest: Any,  # MissionManifest or LoadedMission
        emitter: EventEmitter,
    ) -> AgentTurnResponse:
        """Generate (and persist) one agent turn.

        Wrapped in ``async with db.begin()`` so the AgentTurn insert plus the
        supervision events succeed-or-fail together.
        """
        settings = self._settings
        inner_manifest = getattr(manifest, "manifest", manifest)

        # -- 1. Classify intent + render template (cached per mission) --------
        # The classifier + renderer cache by ``(mission_id, manifest_sha256)``
        # so a hot-reload that bumps the manifest invalidates them. Pass the
        # LoadedMission (which carries the sha) rather than the bare
        # MissionManifest so ``_manifest_sha`` finds it; the bare manifest
        # has no content sha and would now raise.
        classifier = _classifier_for(manifest, mission_folder)
        intent = classifier(prompt)

        renderer = _renderer_for(manifest, mission_folder)
        selected_context = _context_to_list(context)
        failure_mode_title, failure_mode_description = _manifest_failure_mode(inner_manifest)
        seed_response = renderer.render(
            intent=intent,
            prompt=prompt,
            selected_context=selected_context,
            failure_mode_title=failure_mode_title,
            failure_mode_description=failure_mode_description,
        )

        # -- 2. Optional LLM narration ----------------------------------------
        agent_response = seed_response
        source = "template"
        if settings.feature_llm_narration and self.llm.is_available():
            try:
                narrated = await self.llm.narrate(
                    seed=seed_response,
                    prompt_text=prompt[:300],
                    context_summary=", ".join(selected_context[:5]) if selected_context else "",
                    session_id=str(session.id),
                    banned_tokens=_manifest_banned_tokens(inner_manifest),
                )
                if narrated and narrated != seed_response:
                    agent_response = narrated
                    source = "llm"
            except Exception as exc:  # pragma: no cover — narrate handles its own errors
                logger.warning("narration failed, using template: {}", exc)

        agent_responses_total.labels(mission_id=session.mission_id, source=source).inc()

        # -- 3. Atomic persist + emit -----------------------------------------
        async with _safe_begin(db):
            # Atomic claim of the next turn_index via UPDATE ... RETURNING so
            # two concurrent prompts on the same session cannot collide on the
            # ``UNIQUE(session_id, turn_index)`` constraint. ``RETURNING`` is
            # supported on both Postgres and SQLite (>= 3.35), which is the
            # minimum version pinned by the runtime image and CI.
            result = await db.execute(
                update(SessionRow)
                .where(SessionRow.id == session.id)
                .values(agent_turns=SessionRow.agent_turns + 1)
                .returning(SessionRow.agent_turns)
            )
            new_turns = result.scalar_one()
            turn_index = new_turns - 1  # 0-based index for the row we insert

            turn = AgentTurn(
                session_id=session.id,
                turn_index=turn_index,
                user_prompt=prompt,
                selected_context={
                    "files": context.files,
                    "logs": context.logs,
                    "tests": context.tests,
                    "extras": context.extras,
                },
                agent_response=agent_response,
            )
            db.add(turn)
            await db.flush()

            patch_file = _manifest_patch_file(inner_manifest)
            patch_path = (mission_folder / patch_file) if patch_file else None
            patch_available = bool(patch_path and patch_path.exists())
            proposed_actions = ["apply_patch"] if (intent == "fix" and patch_available) else []

            # FE contract requires `text` (not `prompt`) plus `char_count`.
            # `keyword_hits` is optional — populate when the renderer surfaced
            # any matched keywords so the UI can highlight them.
            await emitter.emit(
                session_id=session.id,
                event_type="prompt.submitted",
                payload={
                    "turn_index": turn_index,
                    "text": prompt,
                    "char_count": len(prompt),
                    "intent": intent,
                    "context_files": context.files,
                },
            )
            # `agent.responded` exposes `turn_index` + `response_summary` (first
            # 280 chars) so the timeline can show a snippet without re-fetching
            # the full turn payload.
            response_summary = (agent_response or "")[:280]
            await emitter.emit(
                session_id=session.id,
                event_type="agent.responded",
                payload={
                    "turn_index": turn_index,
                    "response_summary": response_summary,
                    "intent": intent,
                    "source": source,
                    "proposed_actions": proposed_actions,
                    "llm_used": source == "llm",
                    "turn_id": str(turn.id),
                },
            )

            if intent == "fix" and patch_available:
                await emitter.emit(
                    session_id=session.id,
                    event_type="patch.proposed",
                    payload={
                        "turn_index": turn_index,
                        "patch_file": patch_file,
                        "intent": intent,
                        "turn_id": str(turn.id),
                    },
                )

        # Only reflect the new turn counter on the in-memory ORM object AFTER
        # the transaction has committed successfully. If the ``async with``
        # block above raised, the DB UPDATE was rolled back and we must NOT
        # leave the ORM with a bumped value that no longer matches the row.
        session.agent_turns = new_turns

        return AgentTurnResponse(
            id=turn.id,
            session_id=session.id,
            turn_index=turn_index,
            user_prompt=prompt,
            selected_context=context,
            agent_response=agent_response,
            proposed_actions=proposed_actions,
            applied_patch=None,
            patch_applied_at=None,
            created_at=turn.created_at,
        )

    # ------------------------------------------------------------------
    # apply_patch
    # ------------------------------------------------------------------

    async def apply_patch(
        self,
        db: AsyncSession,
        session: SessionRow,
        turn_id: uuid.UUID,
        sandbox_driver: Any,
        sandbox_handle: Any,
        emitter: EventEmitter,
        manifest: Any | None = None,
    ) -> PatchResult:
        """Apply the agent-generated patch for a given turn.

        Wrapped in ``async with db.begin()`` so the turn update plus the
        ``patch.applied`` / ``patch.failed`` event commit atomically. The
        driver invocation is also wrapped in ``asyncio.wait_for`` so a
        runaway docker exec cannot stall the whole pipeline.
        """
        from datetime import UTC, datetime

        settings = self._settings

        # -- Load the AgentTurn ------------------------------------------------
        turn: AgentTurn | None = (
            await db.execute(select(AgentTurn).where(AgentTurn.id == turn_id))
        ).scalar_one_or_none()
        if turn is None:
            return PatchResult(applied=False, error=f"turn {turn_id} not found")

        # -- Idempotency guard -------------------------------------------------
        # A double POST to /patches/{turn_id}/apply must not re-invoke the
        # driver. ``turn.applied_patch`` is the persisted success marker — it is
        # set (alongside ``patch_applied_at``) only after the driver reports a
        # successful apply. If it's already populated the diff is on the
        # workspace; re-running ``apply_diff`` would have ``git apply`` reject
        # the already-applied hunk and emit a spurious ``patch.failed`` for a
        # turn that actually SUCCEEDED, corrupting the grader's replayed event
        # stream. Short-circuit with the prior success and emit NO event.
        #
        # A genuinely-FAILED prior attempt leaves ``applied_patch`` None (the
        # failure branches never touch the row), so it stays retryable.
        if turn.applied_patch is not None:
            return PatchResult(applied=True)

        # -- Locate the diff on disk ------------------------------------------
        mission_folder = _find_mission_folder(session.mission_id, settings.missions_root)
        if mission_folder is None:
            return PatchResult(
                applied=False,
                error=f"mission folder for '{session.mission_id}' not found",
            )

        # Prefer the manifest-declared patch file when supplied (so a mission
        # can ship multiple alternative diffs). Fall back to the historical
        # ``agent_patch.diff`` for callers that don't pass a manifest.
        patch_file = (
            _manifest_patch_file(manifest) if manifest is not None else None
        ) or "agent_patch.diff"
        patch_path = mission_folder / patch_file
        if not patch_path.exists():
            return PatchResult(
                applied=False,
                error=f"patch file not found at {patch_path}",
            )
        # Offload the (potentially-multi-KB) disk read so a slow filesystem
        # doesn't block the asyncio loop while other requests are waiting.
        patch_text = await asyncio.to_thread(patch_path.read_text, encoding="utf-8")
        turn_index = int(getattr(turn, "turn_index", 0) or 0)

        # -- Invoke the sandbox driver ----------------------------------------
        try:
            result = await asyncio.wait_for(
                sandbox_driver.apply_diff(sandbox_handle, patch_text),
                timeout=60,
            )
        except TimeoutError:
            logger.warning("apply_diff timed out for turn {}", turn_id)
            async with _safe_begin(db):
                await emitter.emit(
                    session_id=session.id,
                    event_type="patch.failed",
                    payload=_build_patch_failed_payload(
                        turn_index=turn_index,
                        turn_id=turn_id,
                        error="apply_diff timed out after 60s",
                    ),
                )
            return PatchResult(applied=False, error="apply_diff timed out after 60s")
        except Exception as exc:
            # Keep the full exception (including traceback + driver paths) in
            # the structured log so an operator can debug, but only surface a
            # generic message to the client — the raw ``str(exc)`` can leak
            # internal driver/file paths that the user has no business seeing.
            logger.opt(exception=True).warning("apply_diff raised for turn {}: {}", turn_id, exc)
            generic_error = "patch apply failed — see server logs"
            async with _safe_begin(db):
                await emitter.emit(
                    session_id=session.id,
                    event_type="patch.failed",
                    payload=_build_patch_failed_payload(
                        turn_index=turn_index,
                        turn_id=turn_id,
                        error=generic_error,
                    ),
                )
            return PatchResult(applied=False, error=generic_error)

        if not result.applied:
            files_list = list(result.files_changed)
            async with _safe_begin(db):
                # ``file_count`` (was: ``files_changed``) — P1-B10. The
                # legacy name collided with the
                # :class:`PatchResult.files_changed` list field, confusing
                # readers about whether the payload value was a count or
                # the list itself.
                await emitter.emit(
                    session_id=session.id,
                    event_type="patch.failed",
                    payload=_build_patch_failed_payload(
                        turn_index=turn_index,
                        turn_id=turn_id,
                        error=result.error or "git apply returned non-zero",
                        file_count=len(files_list),
                        added=result.added_lines,
                        removed=result.removed_lines,
                    ),
                )
            return PatchResult(
                applied=False,
                files_changed=files_list,
                added_lines=result.added_lines,
                removed_lines=result.removed_lines,
                error=result.error,
            )

        async with _safe_begin(db):
            now = datetime.now(UTC)
            turn.applied_patch = patch_text
            turn.patch_applied_at = now
            db.add(turn)
            await db.flush()

            files_list = list(result.files_changed)
            await emitter.emit(
                session_id=session.id,
                event_type="patch.applied",
                payload={
                    "turn_index": turn_index,
                    # ``file_count`` (was: ``files_changed``) — P1-B10. The
                    # frontend Timeline + scorer both read this key; the
                    # rename clarifies that the field is an integer count
                    # rather than the list of paths.
                    "file_count": len(files_list),
                    "added": result.added_lines,
                    "removed": result.removed_lines,
                    "turn_id": str(turn_id),
                },
            )

        # Phase 4.A.22 — patch.applied changed the workspace, so the
        # cached ``git ls-files`` listing for this sandbox is now stale.
        # Drop the entry so the next ``GET /sessions/{id}/files/list``
        # re-shells out and picks up the newly-created files. Without
        # this, a successful apply_patch leaves the quick-open palette
        # pinned at the pre-patch file set for up to 30s. Defensive
        # ``getattr`` because test stubs sometimes pass a bare ``object()``
        # as the handle.
        from app.sandbox.file_cache import invalidate_for_sandbox

        sandbox_id_attr = getattr(sandbox_handle, "id", None)
        if isinstance(sandbox_id_attr, str):
            invalidate_for_sandbox(sandbox_id_attr)

        return PatchResult(
            applied=True,
            files_changed=list(result.files_changed),
            added_lines=result.added_lines,
            removed_lines=result.removed_lines,
        )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


class _MissionProxy:
    """Duck-type proxy so intents.load_intents can read folder + manifest."""

    __slots__ = ("folder", "manifest")

    def __init__(self, folder: Path, manifest: Any) -> None:
        self.folder = folder
        self.manifest = manifest


class _NullTxn:
    """No-op async context manager used when a session is already in a tx."""

    async def __aenter__(self) -> _NullTxn:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


def _safe_begin(db: AsyncSession):
    """Return ``db.begin()`` when no transaction is active, else a no-op.

    The FastAPI ``get_db`` dependency commits on exit, but it does NOT open a
    transaction up-front — so under FastAPI, ``db.begin()`` opens one, and on
    exit the dependency's commit becomes a no-op (the transaction was already
    committed). When the caller is a test that pre-opened a transaction or is
    inside ``db.begin()`` themselves, we yield a null context so we don't
    raise ``InvalidRequestError: A transaction is already begun on this
    Session``.
    """
    if db.in_transaction():
        return _NullTxn()
    return db.begin()
