"""Build a per-user data-export zip and upload it to S3 (P0-6).

Entry point :func:`build_user_export` is the RQ task target. It runs in
the worker process (or inline when ``get_queue`` returns ``None`` — see
the account route for the in-line dispatch) and walks every per-user
table to emit a single JSONL file per table inside the zip. The README
inside the zip explains every file in plain English.

Defensive invariant: the worker reads only rows where ``user_id ==
export.user_id`` and asserts ownership before every dump. The zip is
written to a tempfile, uploaded to ``data-exports/{user_id}/{id}.zip``,
and the row is transitioned ``running → ready`` (or ``running → failed``
on any exception).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
import uuid
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.agent_turn import AgentTurn
from app.models.command_run import CommandRun
from app.models.data_export import (
    EXPORT_STATUS_FAILED,
    EXPORT_STATUS_READY,
    EXPORT_STATUS_RUNNING,
    DataExport,
)
from app.models.file_change import FileChange
from app.models.session import SessionRow
from app.models.submission import Submission
from app.models.supervision_event import SupervisionEvent
from app.models.user import User
from app.models.user_badge import UserBadge
from app.observability import data_exports_requested_total
from app.storage import generate_download_url, put_object

# Try to include the user-consent + account-event tables if present
# (older deployments may predate migration 0015 / 0017). We import
# defensively so an older DB without the tables still produces an
# export — the corresponding files just come out empty.
_AccountEventCls: Any | None
_UserConsentCls: Any | None
try:  # pragma: no cover — exercised when the consent / account_events migrations land
    from app.models.user_consent import AccountEvent as _AccountEventImport
    from app.models.user_consent import UserConsent as _UserConsentImport

    _AccountEventCls = _AccountEventImport
    _UserConsentCls = _UserConsentImport
except Exception:  # pragma: no cover
    _AccountEventCls = None
    _UserConsentCls = None


# Columns scrubbed from ``user.json`` because they leak no-value-to-user
# internals (epoch is server-only state; tutorial_replay_count is content-
# tuning telemetry). The README documents the omission.
_SCRUBBED_USER_FIELDS: frozenset[str] = frozenset({"session_epoch", "tutorial_replay_count"})


def _upload_zip(s3_key: str, zip_path: str) -> None:
    """Synchronous upload helper — runs on a thread per asyncio.to_thread."""
    with open(zip_path, "rb") as fh:
        put_object(s3_key, fh, content_type="application/zip")


def _unlink_quiet(path: str) -> None:
    """Best-effort unlink — runs on a thread to keep ASYNC230 happy."""
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass


def _stat_size(path: str) -> int:
    """Return ``Path(path).stat().st_size`` — wrapped so async paths
    don't trip ruff's ASYNC240 (which warns about pathlib in coroutines)."""
    return Path(path).stat().st_size


@dataclass(slots=True)
class _SignContext:
    """Carries the export TTL into the JSONL serialisers so per-row signed
    URLs all share the same expiry — never longer than the export itself."""

    expires_in_seconds: int


def build_user_export(export_id_str: str, *, inline: bool = False) -> None:
    """RQ entry point — synchronous wrapper around the async pipeline.

    ``inline=True`` is set by the REST handler's in-process fallback. In
    that mode the worker MUST NOT re-raise after marking the row failed
    — the route serialises the row state and returns 202, so a raise
    would surface a 500 to the caller even though the failure is already
    captured in the row. RQ workers leave ``inline=False`` so failures
    re-raise and the queue can apply its retry policy.
    """
    asyncio.run(_async_build_user_export(uuid.UUID(export_id_str), inline=inline))


async def _async_build_user_export(export_id: uuid.UUID, *, inline: bool = False) -> None:
    from app.db.session import AsyncSessionLocal

    settings = get_settings()
    ttl_seconds = settings.data_export_ttl_days * 24 * 60 * 60

    async with AsyncSessionLocal() as db:
        export = (
            await db.execute(select(DataExport).where(DataExport.id == export_id))
        ).scalar_one_or_none()
        if export is None:
            logger.warning("account_export: row {} disappeared before build", export_id)
            return

        # Phase 4.A.9 — idempotent skip on terminal states. RQ
        # re-delivery (acknowledgement loss) or a stale in-process
        # fallback re-enqueue could re-fire the worker against an
        # already-built archive. Re-running would either overwrite a
        # known-good zip with a fresher (but identical) one OR flip a
        # failed export back to ``running`` and lose the original
        # ``error`` string. ``queued`` → ``running`` stays the
        # legitimate transition this worker owns.
        if export.status in (EXPORT_STATUS_READY, EXPORT_STATUS_FAILED):
            logger.info(
                "account_export: idempotent skip for export={} status={} (already terminal)",
                export_id,
                export.status,
            )
            return

        user = (
            await db.execute(select(User).where(User.id == export.user_id))
        ).scalar_one_or_none()
        if user is None:
            await _mark_failed(db, export, "user vanished before export ran")
            return

        export.status = EXPORT_STATUS_RUNNING
        await db.commit()

        try:
            zip_path, byte_size = await _build_zip(
                db,
                user=user,
                export_id=export.id,
                sign_ctx=_SignContext(expires_in_seconds=ttl_seconds),
            )
        except Exception as exc:
            logger.exception("account_export: build failed for {}: {}", export_id, exc)
            await _mark_failed(db, export, str(exc).splitlines()[0][:500])
            # Inline callers (the REST handler's no-Redis fallback) have
            # already serialised the row + returned 202; re-raising here
            # would 500 the request even though the failure is recorded
            # on the row. RQ workers leave ``inline=False`` so the queue
            # still sees the raise and can retry.
            if not inline:
                raise
            return

        try:
            s3_key = f"data-exports/{user.id}/{export.id}.zip"
            # Read the zip + push to S3 on a thread — keeps the event
            # loop unblocked even on multi-MB exports, and ASYNC230 stops
            # complaining about ``open`` inside an async function.
            await asyncio.to_thread(_upload_zip, s3_key, zip_path)
        except Exception as exc:
            logger.exception("account_export: upload failed for {}: {}", export_id, exc)
            await _mark_failed(db, export, f"upload failed: {exc}".splitlines()[0][:500])
            await asyncio.to_thread(_unlink_quiet, zip_path)
            if not inline:
                raise
            return

        await asyncio.to_thread(_unlink_quiet, zip_path)

        now = datetime.now(UTC)
        export.status = EXPORT_STATUS_READY
        export.s3_key = s3_key
        export.bytes_total = byte_size
        export.ready_at = now
        export.expires_at = now + timedelta(seconds=ttl_seconds)
        export.error = None
        await db.commit()
        data_exports_requested_total.labels(status=EXPORT_STATUS_READY).inc()
        logger.info(
            "account_export: export {} ready for user {} ({} bytes)",
            export.id,
            user.id,
            byte_size,
        )


async def _mark_failed(db: AsyncSession, export: DataExport, error: str) -> None:
    export.status = EXPORT_STATUS_FAILED
    export.error = error[:500]
    await db.commit()
    data_exports_requested_total.labels(status=EXPORT_STATUS_FAILED).inc()


async def _build_zip(
    db: AsyncSession,
    *,
    user: User,
    export_id: uuid.UUID,
    sign_ctx: _SignContext,
) -> tuple[str, int]:
    """Write the export zip to a tempfile and return (path, bytes).

    Caller is responsible for ``unlink`` after the upload completes — the
    tempfile is deliberately persistent across function boundaries so the
    upload can stream from disk rather than buffering in memory.
    """
    tmp_handle = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    tmp_handle.close()
    tmp_path = tmp_handle.name
    try:
        with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("README.md", _render_readme(user=user, sign_ctx=sign_ctx))
            zf.writestr(
                "user.json",
                json.dumps(_serialise_user(user), indent=2, default=str) + "\n",
            )

            # Each tuple: (filename, ORM model, user_id-column, serialiser).
            # Add new per-user tables here as the schema grows. Every model
            # MUST have a user_id FK; the defensive assertion below catches
            # accidental cross-user leakage.
            jobs: list[tuple[str, Any, str, Any]] = [
                ("sessions.jsonl", SessionRow, "user_id", _serialise_session),
                ("agent_turns.jsonl", AgentTurn, "session_id", _serialise_agent_turn),
                (
                    "file_changes.jsonl",
                    FileChange,
                    "session_id",
                    _serialise_file_change,
                ),
                (
                    "command_runs.jsonl",
                    CommandRun,
                    "session_id",
                    _serialise_command_run(sign_ctx),
                ),
                ("submissions.jsonl", Submission, "session_id", _serialise_submission),
                (
                    "supervision_events.jsonl",
                    SupervisionEvent,
                    "session_id",
                    _serialise_supervision_event,
                ),
                ("badges.jsonl", UserBadge, "user_id", _serialise_user_badge),
            ]

            session_ids = {s.id for s in await _fetch_user_sessions(db, user.id)}

            for filename, model, fk_col, serialiser in jobs:
                rows = await _fetch_rows_for(db, model, fk_col, user.id, session_ids)
                # Defensive ownership cross-check — never let a row whose
                # FK chain doesn't trace back to this user end up in the
                # zip. The route already constrains per user_id; this is
                # a belt-and-braces guarantee against future refactors.
                if fk_col == "user_id":
                    for row in rows:
                        # Explicit raise (not assert) so `python -O` cannot
                        # silently strip the cross-user leakage guard.
                        if row.user_id != user.id:
                            raise RuntimeError(
                                f"export ownership invariant violated for {model.__name__}"
                            )
                else:
                    for row in rows:
                        if row.session_id not in session_ids:
                            raise RuntimeError(
                                f"export session-ownership invariant violated for {model.__name__}"
                            )

                zf.writestr(filename, _to_jsonl(rows, serialiser))

            if _UserConsentCls is not None and _AccountEventCls is not None:
                consents = (
                    (
                        await db.execute(
                            select(_UserConsentCls).where(_UserConsentCls.user_id == user.id)
                        )
                    )
                    .scalars()
                    .all()
                )
                account_events = (
                    (
                        await db.execute(
                            select(_AccountEventCls).where(_AccountEventCls.user_id == user.id)
                        )
                    )
                    .scalars()
                    .all()
                )
                for row in consents:
                    assert row.user_id == user.id
                for row in account_events:
                    assert row.user_id == user.id
                # consents.jsonl keeps the user-consent decisions on its
                # own so existing readers (and the README) can still
                # locate them by the historical filename. account_events
                # .jsonl carries the full account-scoped supervision
                # stream (P0-5 ``consent.*`` + P0-6 ``account.*``).
                zf.writestr(
                    "consents.jsonl",
                    _to_jsonl(consents, _serialise_user_consent),
                )
                zf.writestr(
                    "account_events.jsonl",
                    _to_jsonl(account_events, _serialise_account_event),
                )
            else:
                zf.writestr("consents.jsonl", "")
                zf.writestr("account_events.jsonl", "")

        size = await asyncio.to_thread(_stat_size, tmp_path)
        # Hand the caller the on-disk path so it can stream the upload
        # rather than buffering the whole zip in memory. Caller MUST
        # unlink after the upload finishes.
        return tmp_path, size
    except BaseException:
        # Build failure: clean up the zip we never used. The caller's
        # ``_mark_failed`` branch handles the DB transition; we just
        # don't want the orphan file lingering in /tmp.
        await asyncio.to_thread(_unlink_quiet, tmp_path)
        raise


async def _fetch_user_sessions(db: AsyncSession, user_id: uuid.UUID) -> list[SessionRow]:
    return list(
        (await db.execute(select(SessionRow).where(SessionRow.user_id == user_id))).scalars()
    )


async def _fetch_rows_for(
    db: AsyncSession,
    model: Any,
    fk_col: str,
    user_id: uuid.UUID,
    session_ids: set[uuid.UUID],
) -> list[Any]:
    if fk_col == "user_id":
        stmt = select(model).where(model.user_id == user_id)
    elif fk_col == "session_id":
        if not session_ids:
            return []
        stmt = select(model).where(model.session_id.in_(session_ids))
    else:  # pragma: no cover — defensive
        raise ValueError(f"unsupported fk_col {fk_col}")
    return list((await db.execute(stmt)).scalars())


def _to_jsonl(rows: Iterable[Any], serialiser: Any) -> str:
    lines: list[str] = []
    for row in rows:
        lines.append(json.dumps(serialiser(row), default=str))
    return ("\n".join(lines) + "\n") if lines else ""


# ---------------------------------------------------------------------------
# Per-table serialisers (kept tiny — they project ORM rows to dicts and
# never emit raw S3 keys; instead they sign URLs via _SignContext).
# ---------------------------------------------------------------------------


def _serialise_user(user: User) -> dict[str, Any]:
    base = {c.name: getattr(user, c.name) for c in user.__table__.columns}
    for key in _SCRUBBED_USER_FIELDS:
        base.pop(key, None)
    return base


def _serialise_session(row: SessionRow) -> dict[str, Any]:
    return {c.name: getattr(row, c.name) for c in row.__table__.columns}


def _serialise_agent_turn(row: AgentTurn) -> dict[str, Any]:
    return {c.name: getattr(row, c.name) for c in row.__table__.columns}


def _serialise_file_change(row: FileChange) -> dict[str, Any]:
    return {c.name: getattr(row, c.name) for c in row.__table__.columns}


def _serialise_command_run(sign_ctx: _SignContext):
    def _inner(row: CommandRun) -> dict[str, Any]:
        out = {c.name: getattr(row, c.name) for c in row.__table__.columns}
        # Stdout / stderr that live in S3 are surfaced as signed URLs so
        # the export is self-contained for the duration of its TTL.
        for s3_field in ("stdout_s3_key", "stderr_s3_key"):
            key = out.get(s3_field)
            if isinstance(key, str) and key:
                out[f"{s3_field}_download_url"] = generate_download_url(
                    key, expires_in=sign_ctx.expires_in_seconds
                )
        return out

    return _inner


def _serialise_submission(row: Submission) -> dict[str, Any]:
    return {c.name: getattr(row, c.name) for c in row.__table__.columns}


def _serialise_supervision_event(row: SupervisionEvent) -> dict[str, Any]:
    return {c.name: getattr(row, c.name) for c in row.__table__.columns}


def _serialise_user_badge(row: UserBadge) -> dict[str, Any]:
    return {c.name: getattr(row, c.name) for c in row.__table__.columns}


def _serialise_user_consent(row: Any) -> dict[str, Any]:
    return {c.name: getattr(row, c.name) for c in row.__table__.columns}


def _serialise_account_event(row: Any) -> dict[str, Any]:
    return {c.name: getattr(row, c.name) for c in row.__table__.columns}


# ---------------------------------------------------------------------------
# README rendering
# ---------------------------------------------------------------------------


def _render_readme(*, user: User, sign_ctx: _SignContext) -> str:
    expires_in_days = max(1, sign_ctx.expires_in_seconds // (24 * 60 * 60))
    short = hashlib.sha256(str(user.id).encode()).hexdigest()[:8]
    return f"""# OpenAgentDojo data export

This zip contains every record we store about your account. The signed
URLs inside the JSON files are valid for **{expires_in_days} days** from
the moment the export was generated; after that the URLs return 403 and
this file can be regenerated from the /account page.

## Files

| File | What it contains |
| --- | --- |
| `user.json` | Your profile row, minus internal-only telemetry (`session_epoch`, `tutorial_replay_count`). |
| `sessions.jsonl` | One line per mission attempt (`SessionRow`). |
| `agent_turns.jsonl` | Every prompt you sent + every agent response (`AgentTurn`). |
| `file_changes.jsonl` | Every file the agent or you wrote during a session. |
| `command_runs.jsonl` | Every command run inside a sandbox. Includes signed URLs for stdout / stderr. |
| `submissions.jsonl` | Every graded submission. |
| `supervision_events.jsonl` | Timeline events emitted while you worked. |
| `badges.jsonl` | Badges you earned (`UserBadge`). |
| `consents.jsonl` | Cookie / privacy consent decisions (`UserConsent`). |
| `account_events.jsonl` | Account-scoped audit log: consent transitions + account self-service events (email change, sign-out-everywhere, deletion schedule / cancel). |

## Things to know

* Prompts are stored verbatim. If you intend to share this archive with
  a third party, redact the `prompts` / `agent_response` fields first.
* This archive expires `{expires_in_days}` days from generation. The link
  inside your /account page automatically refreshes after expiry.
* This export does NOT include third-party analytics events (PostHog,
  Resend delivery metadata) — those live with the vendor and are not
  persisted by OpenAgentDojo.
* Account id shorthand: `{short}` (first 8 hex chars of your UUID).
"""
