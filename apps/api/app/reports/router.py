"""Reports REST endpoints.

GET  /api/v1/reports/{submission_id}            — ownership-protected or share-token
POST /api/v1/reports/{submission_id}/share     — owner-only; mints a 30d JWT

The share token is a JWT signed with ``settings.session_secret``. It carries
``sub`` (submission id), ``iat`` and ``exp`` (epoch). Both endpoints render
the persisted :class:`SubmissionRead` enriched with the mission's
``ideal_solution.md`` markdown.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response
from jose import ExpiredSignatureError, JWTError, jwt
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user, require_auth
from app.config import Settings, get_settings
from app.db.session import get_db
from app.missions.resolver import MissionFolderNotFoundError, resolve_mission_dir
from app.missions.service import get_mission as get_mission_row
from app.models.report_render import (
    RENDER_KIND_PDF,
    RENDER_KINDS,
    RENDER_STATUS_FAILED,
    RENDER_STATUS_QUEUED,
    RENDER_STATUS_READY,
    RENDER_STATUS_RUNNING,
    ReportRender,
)
from app.models.session import SessionRow
from app.models.submission import Submission
from app.models.user import User
from app.reports.verification import build_envelope
from app.schemas.auth import ShareTokenRead
from app.schemas.submission import ReportRenderRead, SubmissionRead, VerifyEnvelopeRead
from app.storage import generate_download_url

router = APIRouter(prefix="/reports", tags=["reports"])

# P0-11 — separate router mounted at /verify so the URL the user copies
# into a résumé doesn't carry the /reports path. The router is registered
# alongside the reports router in app/main.py.
verify_router = APIRouter(prefix="/verify", tags=["reports"])

_SHARE_ALG = "HS256"
_SHARE_TTL_DAYS = 30


class ShareTokenError(Exception):
    """Raised when a share token cannot be decoded.

    ``reason`` distinguishes ``"expired"`` from ``"invalid"`` so the route
    can surface a useful 400 body rather than the historical opaque "not
    found".
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


# ---------------------------------------------------------------------------
# Share-token helpers
# ---------------------------------------------------------------------------


def _share_secret(settings: Settings) -> str:
    """Return the share-token signing secret.

    Prefers a dedicated ``share_token_secret`` so rotating session cookies
    doesn't also invalidate every outstanding share URL. Falls back to the
    session secret for back-compat with older deployments.
    """
    return settings.share_token_secret or settings.session_secret


def issue_share_token(submission_id: uuid.UUID, settings: Settings) -> tuple[str, datetime]:
    """Return (token, expires_at) for a 30-day share JWT."""
    now = datetime.now(UTC)
    exp = now + timedelta(days=_SHARE_TTL_DAYS)
    payload = {
        "sub": str(submission_id),
        "kind": "report-share",
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    token = jwt.encode(payload, _share_secret(settings), algorithm=_SHARE_ALG)
    return token, exp


def decode_share_token_strict(token: str, settings: Settings) -> uuid.UUID:
    """Decode a share token or raise :class:`ShareTokenError`."""
    try:
        payload = jwt.decode(token, _share_secret(settings), algorithms=[_SHARE_ALG])
    except ExpiredSignatureError as exc:
        raise ShareTokenError("expired", "share link has expired") from exc
    except JWTError as exc:
        raise ShareTokenError("invalid", "share link signature invalid") from exc
    if payload.get("kind") != "report-share":
        raise ShareTokenError("invalid", "share link is for a different resource")
    sub = payload.get("sub")
    if not isinstance(sub, str):
        raise ShareTokenError("invalid", "share link payload malformed")
    try:
        return uuid.UUID(sub)
    except ValueError as exc:
        raise ShareTokenError("invalid", "share link payload malformed") from exc


def decode_share_token(token: str, settings: Settings) -> uuid.UUID | None:
    """Lenient wrapper that returns None on any decode failure (back-compat)."""
    try:
        return decode_share_token_strict(token, settings)
    except ShareTokenError:
        return None


# ---------------------------------------------------------------------------
# Mission-folder + ideal-solution lookup
# ---------------------------------------------------------------------------


def _find_mission_folder(missions_root: Path, mission_id: str) -> Path | None:
    try:
        folder = resolve_mission_dir(missions_root, mission_id)
    except (MissionFolderNotFoundError, ValueError) as exc:
        logger.debug("[reports] could not resolve mission folder for {}: {}", mission_id, exc)
        return None
    if not (folder / "mission.yaml").exists():
        return None
    return folder


def _read_ideal_solution(missions_root: Path, mission_id: str) -> str | None:
    folder = _find_mission_folder(missions_root, mission_id)
    if folder is None:
        return None
    ideal_path = folder / "ideal_solution.md"
    try:
        return ideal_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except Exception as exc:
        logger.warning("[reports] could not read ideal_solution.md for {}: {}", mission_id, exc)
        return None


def _read_ideal_solution_diff(missions_root: Path, mission_id: str) -> str | None:
    """Return the canonical fix as a unified diff (P0-2).

    Missions backfilled by ``scripts/extract_ideal_diffs.py``; the
    validator requires it on every non-tutorial mission. Returns None
    when the file is absent (tutorial missions) so the FE can branch on
    that to hide the three-way diff layer.
    """
    folder = _find_mission_folder(missions_root, mission_id)
    if folder is None:
        return None
    diff_path = folder / "ideal_solution.diff"
    try:
        return diff_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except Exception as exc:
        logger.warning("[reports] could not read ideal_solution.diff for {}: {}", mission_id, exc)
        return None


def _read_agent_patch_diff(missions_root: Path, mission_id: str) -> str | None:
    """Return the agent's original (deliberately-flawed) patch (P0-2)."""
    folder = _find_mission_folder(missions_root, mission_id)
    if folder is None:
        return None
    # Manifest declares the exact file name; we resolve via the loader so
    # custom mission packs with non-default ``patch_file`` keys still
    # render the three-way diff correctly.
    try:
        from app.missions.loader import MissionLoader

        loader = MissionLoader(missions_root)
        loaded = loader.load_manifest(folder / "mission.yaml")
        patch_path = folder / loaded.manifest.agent.patch_file
        return patch_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except Exception as exc:
        logger.warning("[reports] could not read agent_patch.diff for {}: {}", mission_id, exc)
        return None


# ---------------------------------------------------------------------------
# Loader: fetch (submission, session) by id
# ---------------------------------------------------------------------------


async def _fetch_submission(
    db: AsyncSession, submission_id: uuid.UUID
) -> tuple[Submission, SessionRow]:
    submission = (
        await db.execute(select(Submission).where(Submission.id == submission_id))
    ).scalar_one_or_none()
    if submission is None:
        raise HTTPException(status_code=404, detail="submission not found")

    session = (
        await db.execute(select(SessionRow).where(SessionRow.id == submission.session_id))
    ).scalar_one_or_none()
    if session is None:
        # Submission orphaned (shouldn't happen); treat as 404.
        raise HTTPException(status_code=404, detail="session not found")

    return submission, session


def _to_read_model(
    submission: Submission,
    ideal_solution: str | None,
    status: str,
    *,
    ideal_solution_diff: str | None = None,
    agent_patch_diff: str | None = None,
    mission_id: str | None = None,
) -> SubmissionRead:
    """Materialise the wire-format SubmissionRead.

    Three additional surfaces are injected at read time (none are stored
    on the row):
      * ``ideal_solution``       — the markdown narrative (legacy).
      * ``ideal_solution_diff``  — the canonical fix as a unified diff (P0-2).
      * ``agent_patch_diff``     — the agent's original patch (P0-2).

    All three are gated on ``session.status == 'graded'`` so a mid-pipeline
    crash doesn't leak the answer.

    ``mission_id`` (P0-3) is sourced from the joined session row and is
    NOT gated on the graded state — the Retry CTA needs the id even on
    error/abandoned reports so the user can spin up a clean attempt.
    """
    data = SubmissionRead.model_validate(submission)
    gated = status == "graded"
    return data.model_copy(
        update={
            "ideal_solution": ideal_solution if gated else None,
            "ideal_solution_diff": ideal_solution_diff if gated else None,
            "agent_patch_diff": agent_patch_diff if gated else None,
            "mission_id": mission_id,
        }
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "/{submission_id}",
    response_model=SubmissionRead,
    summary="Retrieve a grading report by submission id",
)
async def get_report(
    submission_id: uuid.UUID,
    share: str | None = Query(None, description="Optional signed share token"),
    user: User | None = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SubmissionRead:
    """Return a graded submission.

    Authorisation succeeds if either of:
      - The caller is signed in AND owns the submission's session.
      - A valid ``?share=<jwt>`` query param decodes to this submission id.

    All other callers receive 403 (or 401 when neither cookie nor share is
    present).
    """
    settings = get_settings()
    submission, session = await _fetch_submission(db, submission_id)

    share_ok = False
    # Stash the share-decode error rather than raising inline: a malformed or
    # expired ?share= must NOT shadow a perfectly good cookie auth. Only
    # surface the 400 if cookie auth would also have failed (P2-8).
    share_error: HTTPException | None = None
    if share:
        try:
            share_sub = decode_share_token_strict(share, settings)
            share_ok = share_sub == submission_id
            if not share_ok:
                share_error = HTTPException(
                    status_code=400,
                    detail={
                        "reason": "invalid",
                        "message": "share link is for a different submission",
                    },
                )
        except ShareTokenError as exc:
            share_error = HTTPException(
                status_code=400,
                detail={"reason": exc.reason, "message": str(exc)},
            )

    if not share_ok:
        if user is None:
            # No share auth, no cookie. Prefer the more-specific share-decode
            # error when the caller attached a token; fall back to the usual
            # 401 otherwise.
            if share_error is not None:
                raise share_error
            raise HTTPException(status_code=401, detail="authentication required")
        if session.user_id != user.id:
            # Cookie present but does not own the submission AND the share
            # didn't validate either — surface the share-decode error if any,
            # otherwise the generic 403.
            if share_error is not None:
                raise share_error
            raise HTTPException(status_code=403, detail="not your report")

    ideal = _read_ideal_solution(settings.missions_root, session.mission_id)
    ideal_diff = _read_ideal_solution_diff(settings.missions_root, session.mission_id)
    agent_diff = _read_agent_patch_diff(settings.missions_root, session.mission_id)
    return _to_read_model(
        submission,
        ideal,
        session.status,
        ideal_solution_diff=ideal_diff,
        agent_patch_diff=agent_diff,
        mission_id=session.mission_id,
    )


@router.post(
    "/{submission_id}/share",
    response_model=ShareTokenRead,
    summary="Mint a 30-day share token for a report",
)
async def post_share(
    submission_id: uuid.UUID,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> ShareTokenRead:
    """Owner-only: returns the share URL and token (TTL 30d)."""
    settings = get_settings()
    _submission, session = await _fetch_submission(db, submission_id)

    if session.user_id != user.id:
        raise HTTPException(status_code=403, detail="not your report")

    token, expires_at = issue_share_token(submission_id, settings)
    base = (settings.web_origin or "http://localhost:3000").rstrip("/")
    share_url = f"{base}/report/{submission_id}?share={token}"

    return ShareTokenRead(
        share_token=token,
        share_url=share_url,
        expires_at=expires_at,
    )


# ---------------------------------------------------------------------------
# P0-11 — public verification endpoint
# ---------------------------------------------------------------------------


def _resolve_user_for_envelope(db_user: User | None) -> User | None:
    """Identity passthrough — kept as a helper so a future tombstone
    transformation has one obvious place to land."""
    return db_user


async def _load_envelope_inputs(
    db: AsyncSession, submission_id: uuid.UUID
) -> tuple[Submission, SessionRow, User | None, Any]:
    """Fetch the four rows the envelope builder needs.

    Returns ``(submission, session, user, mission_row)`` — ``mission_row``
    is the catalog row (NOT the manifest); the verify endpoint reads
    ``initial_commit`` and ``title`` from the manifest cache when the
    mission folder is present.
    """
    submission, session = await _fetch_submission(db, submission_id)
    user_row = (
        await db.execute(select(User).where(User.id == session.user_id))
    ).scalar_one_or_none()
    mission_row = await get_mission_row(db, session.mission_id)
    return submission, session, _resolve_user_for_envelope(user_row), mission_row


def _is_tutorial(mission_row: Any) -> bool:
    """Tutorials are not credentials — they 404 on /verify."""
    if mission_row is None:
        return False
    return getattr(mission_row, "kind", "standard") == "tutorial"


@verify_router.get(
    "/{submission_id}",
    response_model=VerifyEnvelopeRead,
    summary="Public verification page envelope (P0-11)",
)
async def get_verify(
    submission_id: uuid.UUID,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> VerifyEnvelopeRead:
    """Anonymous endpoint that renders the verification envelope.

    No auth required — the URL is the credential. Returns 404 when:
      * the submission does not exist,
      * the session is not yet graded (mid-pipeline),
      * the mission is a tutorial (tutorials are not credentialing),
      * the verification hash + signature were never stamped.

    The response is cacheable for a year + immutable: a graded
    submission's envelope is frozen forever (the hash pins it). The
    ``X-Robots-Tag`` header tells crawlers the URL is intended to be
    indexed — that's the verification path a recruiter follows when they
    Google the URL.
    """
    settings = get_settings()
    submission, session, user, mission_row = await _load_envelope_inputs(db, submission_id)

    if session.status != "graded":
        raise HTTPException(status_code=404, detail="submission not verifiable")
    if _is_tutorial(mission_row):
        raise HTTPException(status_code=404, detail="tutorials are not credentialing")
    if not submission.verification_hash or not submission.verification_signature:
        # Older grade that predates the stamping path, OR a misconfigured
        # secret on the grade server. The /verify surface refuses to
        # invent values; operators run scripts/backfill_verification.py
        # to re-stamp historical rows.
        raise HTTPException(
            status_code=404,
            detail="submission has no verification signature on file",
        )

    # Re-derive the envelope so the response carries the field set
    # exactly as it was at grade time. The hash + signature come from
    # the persisted columns — never recomputed — so a future change to
    # the envelope builder cannot invalidate an issued credential.
    from app.missions.cache import cached_manifests
    from app.reports.verification import (
        compute_hash,
        compute_signature,
        verify_secret,
    )

    loaded = cached_manifests().get(session.mission_id)
    manifest = loaded.manifest if loaded is not None else None
    envelope = build_envelope(
        submission=submission,
        session=session,
        manifest=manifest,
        user=user,
        mission_row=mission_row,
    )

    # P1-5 — re-verify the HMAC on every GET so a retired ``VERIFY_SECRET``
    # cannot keep serving a credential as valid. The recomputed hash is
    # over the freshly-derived envelope (same inputs → same bytes on
    # every replay); the recomputed signature uses the CURRENT secret.
    # When the persisted signature was produced under a previous secret,
    # ``hmac.compare_digest`` returns False and the credential is gone
    # — the owner can re-seal it from their profile to re-sign under
    # the active secret. ``410 GONE`` (rather than 404) signals
    # "resource existed but is intentionally retired" so a recruiter's
    # client can render an actionable message rather than the generic
    # not-found view.
    try:
        current_secret = verify_secret(settings)
    except RuntimeError as exc:
        logger.error("verify endpoint cannot resolve secret: {}", exc)
        raise HTTPException(
            status_code=503,
            detail={
                "code": "verification_secret_unavailable",
                "message": "verification service is misconfigured; try again later",
            },
        ) from exc

    recomputed_hash = compute_hash(envelope)
    recomputed_signature = compute_signature(recomputed_hash, current_secret)
    import hmac as _hmac

    if not _hmac.compare_digest(recomputed_signature, submission.verification_signature or ""):
        # The persisted signature does NOT match a signature produced
        # under the current secret. Either the secret rotated since
        # this credential was sealed, or the envelope inputs the
        # builder reads today have drifted from what was hashed at
        # grade time. Either way the credential is no longer
        # verifiable from this server; the owner has to re-seal it.
        raise HTTPException(
            status_code=410,
            detail={
                "code": "verification_secret_rotated",
                "message": (
                    "This credential was signed with a retired secret. "
                    "The owner can re-seal it from their profile."
                ),
            },
        )

    base = (settings.web_origin or "http://localhost:3000").rstrip("/")
    canonical_url = f"{base}/verify/{submission_id}"

    # Long-lived cache: graded submissions are immutable.
    response.headers["Cache-Control"] = "public, max-age=31536000, s-maxage=31536000, immutable"
    response.headers["X-Robots-Tag"] = "index, follow"

    return VerifyEnvelopeRead(
        **envelope,
        canonical_url=canonical_url,
        verification_hash=submission.verification_hash,
        verification_signature=submission.verification_signature,
    )


# ---------------------------------------------------------------------------
# P0-11 — PDF / PNG render pipeline
# ---------------------------------------------------------------------------


_RENDER_SIGNED_URL_TTL_SECONDS: int = 5 * 60  # 5 minutes
_RENDER_POLL_AFTER_SECONDS: int = 5


def _content_type_for_kind(kind: str) -> str:
    if kind == RENDER_KIND_PDF:
        return "application/pdf"
    return "image/png"


def _render_s3_key(submission_id: uuid.UUID, kind: str) -> str:
    """Single source of the storage key — used by the worker and the
    download redirect. Kept here (in the route) so the route survives a
    worker refactor that swaps the upload backend."""
    ext = "pdf" if kind == RENDER_KIND_PDF else "png"
    return f"report-renders/{submission_id}/{kind}.{ext}"


def _validate_kind(kind: str) -> str:
    if kind not in RENDER_KINDS:
        raise HTTPException(status_code=422, detail=f"kind must be one of {sorted(RENDER_KINDS)}")
    return kind


async def _auth_render_view(
    submission_id: uuid.UUID,
    share: str | None,
    user: User | None,
    db: AsyncSession,
) -> tuple[Submission, SessionRow]:
    """Mirror ``get_report`` auth: owner OR ?share=<jwt>.

    Additionally gates the response on ``session.status == 'graded'`` —
    the render bytes embed the score + verification hash, and surfacing
    an unfinished session's preview would let a recruiter download a
    PDF that doesn't yet match the persisted envelope. Mid-pipeline
    crashes (``status='error'``) and active sessions both 409. Tutorial
    submissions also 409 because tutorials are not credentialing — they
    have no envelope to render.
    """
    settings = get_settings()
    submission, session = await _fetch_submission(db, submission_id)

    share_ok = False
    share_error: HTTPException | None = None
    if share:
        try:
            share_sub = decode_share_token_strict(share, settings)
            share_ok = share_sub == submission_id
            if not share_ok:
                share_error = HTTPException(
                    status_code=400,
                    detail={
                        "reason": "invalid",
                        "message": "share link is for a different submission",
                    },
                )
        except ShareTokenError as exc:
            share_error = HTTPException(
                status_code=400, detail={"reason": exc.reason, "message": str(exc)}
            )

    if not share_ok:
        if user is None:
            if share_error is not None:
                raise share_error
            raise HTTPException(status_code=401, detail="authentication required")
        if session.user_id != user.id:
            if share_error is not None:
                raise share_error
            raise HTTPException(status_code=403, detail="not your report")

    # Mirror /verify: never produce a render artefact for a non-graded
    # session or a tutorial. Both surfaces would generate a PDF that
    # doesn't anchor to a verifiable envelope.
    if session.status != "graded":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "not_graded",
                "message": "submission is not yet graded",
            },
        )
    mission_row = await get_mission_row(db, session.mission_id)
    if _is_tutorial(mission_row):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "not_graded",
                "message": "tutorials are not credentialing and cannot be rendered",
            },
        )

    return submission, session


# Phase 4.A.21 — module-level anchor for fire-and-forget render tasks.
# Without a strong reference, Python's garbage collector can finalise an
# ``asyncio.create_task`` whose return value is unused; the task gets
# silently cancelled mid-render and the row is stuck at ``queued``
# forever. Mirrors the pattern in ``app/workers/provision.py``.
_BACKGROUND_TASKS: set[Any] = set()


def _enqueue_render(render_id: uuid.UUID) -> None:
    """Hand the render to RQ, with an in-process fallback when Redis is
    not available (mirrors the account_export pattern).

    The fallback path used to call ``render_report(..., inline=True)``
    synchronously, but ``render_report`` wraps the async pipeline in
    ``asyncio.run`` — which raises ``RuntimeError: asyncio.run() cannot
    be called from a running event loop`` when invoked from inside
    FastAPI's request handler. Instead we schedule the underlying
    coroutine on the *current* loop via ``asyncio.create_task`` so it
    runs alongside the response without nesting a fresh loop. The
    worker's own ``inline=True`` branch already swallows exceptions, so
    a failed render lands the row at ``failed`` instead of leaking back
    into the route as a 500.

    Phase 4.A.21 — the create_task return value is now added to a
    module-level set and removed via ``add_done_callback`` so the GC
    cannot finalise the task prematurely.
    """
    import asyncio

    from app.workers.queue import get_queue

    queue = get_queue()
    if queue is None:
        from app.workers.report_render import _async_render

        # Fire-and-forget — the route has already committed the queued
        # row, so the FE polls the row state and the task updates it
        # out-of-band. No nested asyncio.run, no event-loop reentry.
        task = asyncio.create_task(_async_render(render_id, inline=True))
        _BACKGROUND_TASKS.add(task)
        task.add_done_callback(_BACKGROUND_TASKS.discard)
        return
    queue.enqueue("app.workers.report_render.render_report", str(render_id), job_timeout=300)


async def _force_renders_today(db: AsyncSession, submission_id: uuid.UUID) -> int:
    """Count user-initiated force re-renders (trailing 24h, Phase 4.A.20).

    Filters on ``force=True`` so a freshly-graded report's automatic
    first render (fired by ``GET /reports/{id}/render`` on a missing
    row) doesn't burn the user's force-rerender budget. The column
    landed in migration 0024; legacy rows default to ``False`` so the
    historical mis-accounting silently corrects itself on the next
    user-initiated force.
    """
    from datetime import UTC, datetime, timedelta

    horizon = datetime.now(UTC) - timedelta(days=1)
    rows = (
        (
            await db.execute(
                select(ReportRender).where(
                    ReportRender.submission_id == submission_id,
                    ReportRender.created_at >= horizon,
                    ReportRender.force.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    return len(rows)


@router.get(
    "/{submission_id}/render",
    summary="Retrieve or queue a PDF/PNG render (P0-11)",
    responses={
        302: {"description": "Render is ready — Location header carries the signed URL"},
        202: {"description": "Render is queued or running"},
        503: {"description": "Render failed; retry via POST /render"},
    },
)
async def get_render(
    submission_id: uuid.UUID,
    kind: str = Query("pdf", description="render kind: pdf or png"),
    share: str | None = Query(None, description="Optional signed share token"),
    user: User | None = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Owner-or-share endpoint that returns a signed URL when ready.

    Lifecycle:
      * row missing → enqueue a fresh render → 202
      * status=queued|running → 202 with poll_after_seconds
      * status=ready → 302 to a 5-minute signed R2 URL
      * status=failed → 503 with the worker's error message
    """
    kind = _validate_kind(kind)
    _submission, _session = await _auth_render_view(submission_id, share, user, db)

    row = (
        await db.execute(
            select(ReportRender).where(
                ReportRender.submission_id == submission_id,
                ReportRender.kind == kind,
            )
        )
    ).scalar_one_or_none()

    if row is None:
        # No render exists yet — enqueue the job and return 202. We
        # insert the row inside this request so a concurrent GET sees
        # the queued state instead of double-enqueuing.
        row = ReportRender(submission_id=submission_id, kind=kind, status=RENDER_STATUS_QUEUED)
        db.add(row)
        await db.flush()
        _enqueue_render(row.id)
        await db.commit()
        await db.refresh(row)

    if row.status == RENDER_STATUS_READY and row.s3_key:
        signed = generate_download_url(row.s3_key, expires_in=_RENDER_SIGNED_URL_TTL_SECONDS)
        return RedirectResponse(url=signed, status_code=302)
    if row.status == RENDER_STATUS_FAILED:
        return Response(
            status_code=503,
            media_type="application/json",
            content=ReportRenderRead.model_validate(row).model_dump_json(),
        )
    # queued or running — 202.
    payload = ReportRenderRead.model_validate(row).model_copy(
        update={"poll_after_seconds": _RENDER_POLL_AFTER_SECONDS}
    )
    return Response(
        status_code=202,
        media_type="application/json",
        content=payload.model_dump_json(),
    )


class ForceRenderBody(BaseModel):
    """Body for ``POST /reports/{id}/render`` (Phase 4.A.24 rename + tighten).

    ``kind`` is now a ``Literal["pdf", "png"]`` so OpenAPI publishes
    the legal enumeration; an invalid value 422s at validation time
    instead of falling through to ``_validate_kind`` (which still
    runs as a belt-and-braces double-check).
    """

    kind: Literal["pdf", "png"]


@router.post(
    "/{submission_id}/render",
    response_model=ReportRenderRead,
    status_code=202,
    summary="Force re-render a PDF/PNG (owner only) (P0-11)",
)
async def post_render(
    submission_id: uuid.UUID,
    body: ForceRenderBody,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> ReportRenderRead:
    """Owner-only: drop any existing row + enqueue a fresh render.

    Idempotent during ``queued`` / ``running`` — a second call sees the
    in-flight row and returns it without re-enqueuing. Rate-limited at
    ``settings.report_render_force_daily_cap`` force re-renders per
    submission per 24h so a user can't cycle the cached PDF.
    """
    settings = get_settings()
    kind = _validate_kind(body.kind)
    _submission, session = await _fetch_submission(db, submission_id)
    if session.user_id != user.id:
        raise HTTPException(status_code=403, detail="not your report")

    # Render endpoints must gate on graded state — see ``_auth_render_view``
    # for the same guard on GET. Without this, a force-render against an
    # in-progress or errored session would queue a job whose PDF can't
    # anchor to a verification envelope.
    if session.status != "graded":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "not_graded",
                "message": "submission is not yet graded",
            },
        )
    mission_row = await get_mission_row(db, session.mission_id)
    if _is_tutorial(mission_row):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "not_graded",
                "message": "tutorials are not credentialing and cannot be rendered",
            },
        )

    existing = (
        await db.execute(
            select(ReportRender).where(
                ReportRender.submission_id == submission_id,
                ReportRender.kind == kind,
            )
        )
    ).scalar_one_or_none()

    if existing is not None and existing.status in {
        RENDER_STATUS_QUEUED,
        RENDER_STATUS_RUNNING,
    }:
        # Idempotent during in-flight — return the existing row.
        return ReportRenderRead.model_validate(existing).model_copy(
            update={"poll_after_seconds": _RENDER_POLL_AFTER_SECONDS}
        )

    # Rate-limit: count the renders created in the trailing 24 hours.
    if (await _force_renders_today(db, submission_id)) >= settings.report_render_force_daily_cap:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "force_render_rate_limited",
                "message": (
                    f"force re-render cap of "
                    f"{settings.report_render_force_daily_cap}/day reached; "
                    "try again later"
                ),
            },
        )

    if existing is None:
        row = ReportRender(
            submission_id=submission_id,
            kind=kind,
            status=RENDER_STATUS_QUEUED,
            # Phase 4.A.20 — stamp ``force=True`` so the daily cap
            # counter trips ONLY against user-initiated re-renders.
            force=True,
        )
        db.add(row)
    else:
        # Recycle the row identity so the FE's poll URL stays stable.
        existing.status = RENDER_STATUS_QUEUED
        existing.s3_key = None
        existing.bytes = None
        existing.error = None
        existing.ready_at = None
        # Phase 4.A.20 — even on the recycle path this is a user-
        # initiated force, so stamp the column.
        existing.force = True
        row = existing

    await db.flush()
    _enqueue_render(row.id)
    await db.commit()
    await db.refresh(row)

    return ReportRenderRead.model_validate(row).model_copy(
        update={"poll_after_seconds": _RENDER_POLL_AFTER_SECONDS}
    )


# ---------------------------------------------------------------------------
# P0-11 — internal print endpoint (consumed by the report-render worker)
# ---------------------------------------------------------------------------


def _verify_render_token(
    *,
    submission_id: uuid.UUID,
    token: str,
    secret: str,
) -> bool:
    """Constant-time-ish check that ``token`` is an HMAC over (submission_id, *).

    The worker computes ``hmac_sha256(VERIFY_SECRET, f"{submission_id}|{render_id}")``
    but the route doesn't know the render_id at GET time. The simpler
    contract: the worker also sends a render_id-agnostic token (HMAC
    over submission_id only) along with the per-render token; the
    route accepts EITHER. This module only checks the submission-id
    HMAC because the per-render id is private to the worker.
    """
    import hashlib
    import hmac

    expected = hmac.new(
        secret.encode("utf-8"),
        str(submission_id).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, token or "")


@router.get(
    "/{submission_id}/print",
    response_model=SubmissionRead,
    summary="Internal: full report payload for the print-mode worker (P0-11)",
)
async def get_report_for_print(
    submission_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> SubmissionRead:
    """Worker-only endpoint that returns the same SubmissionRead shape as
    ``GET /reports/{id}`` but authorises by ``X-Render-Token`` header
    instead of by session ownership / share token. The token is an
    HMAC over the submission id signed with ``VERIFY_SECRET``.

    The route is intentionally undocumented in the public surface — the
    worker's bridge constructs the URL itself and the FE never hits it.
    """
    settings = get_settings()
    from app.reports.verification import verify_secret as resolve_verify_secret

    token = request.headers.get("X-Render-Token", "")
    if not token or not _verify_render_token(
        submission_id=submission_id,
        token=token,
        secret=resolve_verify_secret(settings),
    ):
        raise HTTPException(status_code=404, detail="not found")

    submission, session = await _fetch_submission(db, submission_id)
    ideal = _read_ideal_solution(settings.missions_root, session.mission_id)
    ideal_diff = _read_ideal_solution_diff(settings.missions_root, session.mission_id)
    agent_diff = _read_agent_patch_diff(settings.missions_root, session.mission_id)
    return _to_read_model(
        submission,
        ideal,
        session.status,
        ideal_solution_diff=ideal_diff,
        agent_patch_diff=agent_diff,
        mission_id=session.mission_id,
    )


__all__ = [
    "decode_share_token",
    "issue_share_token",
    "router",
    "verify_router",
]
