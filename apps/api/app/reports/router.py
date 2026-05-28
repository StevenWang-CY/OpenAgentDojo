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

# P1-6 — separate router for the replay endpoints. Mounted at
# ``/api/v1/submissions/...`` per the design so the URL reads "this
# resource is the submission" instead of "this is a report subresource".
# Both endpoints reuse the same share-token / cookie auth matrix as the
# /reports surface above.
submissions_router = APIRouter(prefix="/submissions", tags=["reports"])

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


# ---------------------------------------------------------------------------
# P1-6 — replay artefact endpoints (JSON + ZIP)
# ---------------------------------------------------------------------------
#
# Two endpoints, one builder. The artefact is built on the fly (no S3
# caching) and is small enough (< 100 KB typical per design) that
# in-memory zip assembly stays within the request budget. Both endpoints
# share the same auth matrix as /reports/{id}: owner OR share-token OR
# 404 for anonymous callers. Tutorials and non-graded submissions 404
# unconditionally so the replay surface stays a credentialing artefact.

# Single-slot cache keyed by ``"env"``; module-level dict lets us avoid the
# ``global`` statement (which ``ruff`` flags as discouraged) while keeping
# the env lazily built on first use.
_REPLAY_JINJA_CACHE: dict[str, Any] = {}


def _replay_jinja_env() -> Any:
    """Lazily build the Jinja2 environment for the replay templates.

    The templates live in ``app/reports/static/`` and are loaded with
    autoescape ON for the HTML template (XSS defence — handles and
    mission titles flow into the page from DB rows) and OFF for the
    Markdown template (Markdown's own escape rules apply).
    """
    env = _REPLAY_JINJA_CACHE.get("env")
    if env is None:
        from jinja2 import Environment, FileSystemLoader, select_autoescape

        templates_dir = Path(__file__).parent / "static"
        env = Environment(
            loader=FileSystemLoader(str(templates_dir)),
            autoescape=select_autoescape(("html", "htm", "xml")),
            keep_trailing_newline=True,
        )
        _REPLAY_JINJA_CACHE["env"] = env
    return env


async def _auth_replay_view(
    submission_id: uuid.UUID,
    share: str | None,
    user: User | None,
    db: AsyncSession,
) -> tuple[Submission, SessionRow, bool]:
    """Mirror ``_auth_render_view`` auth + add the replay-specific gates.

    Returns ``(submission, session, redact_payloads)`` where
    ``redact_payloads`` is True for share-token callers (privacy matrix
    in P1_DESIGN §P1-6 "Privacy posture").

    Differs from /render in two ways:

      * Anonymous callers (no cookie, no share) get **404**, not 401.
        The replay URL is structurally a credential and we leak no
        information about whether the submission exists.
      * Non-graded sessions and tutorial missions both 404 (rather
        than 409 as /render does) — the design says the replay
        endpoint is a credentialing artefact and "credential does not
        exist" is the right HTTP-level posture for a non-credential.
    """
    settings = get_settings()
    submission, session = await _fetch_submission(db, submission_id)

    is_owner = False
    is_share = False
    if user is not None and session.user_id == user.id:
        is_owner = True
    if share:
        try:
            share_sub = decode_share_token_strict(share, settings)
            if share_sub == submission_id:
                is_share = True
        except ShareTokenError:
            # A malformed share token on the replay surface collapses
            # to "anonymous" — we never leak whether the underlying
            # submission exists by surfacing the share-decode error.
            is_share = False

    if not (is_owner or is_share):
        raise HTTPException(status_code=404, detail="replay not found")

    if session.status != "graded":
        raise HTTPException(status_code=404, detail="replay not found")
    mission_row = await get_mission_row(db, session.mission_id)
    if _is_tutorial(mission_row):
        raise HTTPException(status_code=404, detail="replay not found")

    # Owners always see the unredacted artefact. Share tokens get the
    # privacy-redacted form per the matrix.
    redact = (not is_owner) and is_share
    return submission, session, redact


def _replay_etag(artefact: dict[str, Any]) -> str:
    """Return the weak ETag header for a built artefact.

    Weak because the ``exported_at`` field is part of the body but
    excluded from the signature; two reads a second apart produce
    bytes that differ only in that field but are semantically
    identical. The ``W/"replay-..."`` prefix is the standard weak
    marker that lets a downstream CDN compare on the signature alone.
    """
    sig = artefact.get("replay_signature") or ""
    return f'W/"replay-{sig}"'


def _replay_canonical_url(settings: Settings, submission_id: uuid.UUID) -> str:
    base = (settings.web_origin or "http://localhost:3000").rstrip("/")
    return f"{base}/verify/{submission_id}"


def _read_text_safe(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("[replay] could not read {}: {}", path, exc)
        return None


def _render_verify_html(artefact: dict[str, Any], canonical_url: str) -> str:
    envelope = artefact.get("envelope") or {}
    env = _replay_jinja_env()
    template = env.get_template("verify.html.j2")
    rendered: str = template.render(
        canonical_url=canonical_url,
        effective_max=envelope.get("effective_max", 100),
        exported_at=artefact.get("exported_at", ""),
        graded_at=envelope.get("graded_at", ""),
        handle=envelope.get("handle", ""),
        kind=artefact.get("kind", ""),
        mission_id=envelope.get("mission_id", ""),
        mission_title=envelope.get("mission_title", ""),
        rubric_version=envelope.get("rubric_version", ""),
        schema_version=artefact.get("schema_version", 1),
        submission_id=artefact.get("submission_id", ""),
        total_score=envelope.get("total_score", 0),
        verification_hash=envelope.get("verification_hash")
        or _persisted_hash_for(artefact),
        verification_signature=artefact.get("envelope_signature", ""),
    )
    return rendered


def _persisted_hash_for(artefact: dict[str, Any]) -> str:
    """Re-derive the canonical hash for the embedded envelope.

    The envelope on the replay artefact does NOT carry the hash field
    (that lives on the submission row). The verify.html page wants to
    display it, so we recompute it from the embedded envelope using
    the same primitive the /verify endpoint uses. Pure CPU — no I/O,
    no LLM, no secret material.
    """
    from app.reports.verification import compute_hash

    envelope = artefact.get("envelope") or {}
    if not envelope:
        return ""
    try:
        return compute_hash(envelope)
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("[replay] could not recompute envelope hash: {}", exc)
        return ""


def _render_readme(
    artefact: dict[str, Any],
    *,
    has_agent_patch: bool,
) -> str:
    envelope = artefact.get("envelope") or {}
    pointer = artefact.get("mission_pointer") or {}
    env = _replay_jinja_env()
    template = env.get_template("README.md.j2")
    rendered: str = template.render(
        effective_max=envelope.get("effective_max", 100),
        exported_at=artefact.get("exported_at", ""),
        graded_at=envelope.get("graded_at", ""),
        handle=envelope.get("handle", ""),
        has_agent_patch=has_agent_patch,
        kind=artefact.get("kind", ""),
        manifest_sha256=pointer.get("manifest_sha256", ""),
        mission_id=envelope.get("mission_id", ""),
        mission_title=envelope.get("mission_title", ""),
        mission_version=pointer.get("version", 1),
        repo_pack_id=pointer.get("repo_pack_id", ""),
        repo_pack_sha=pointer.get("repo_pack_sha", ""),
        rubric_version=envelope.get("rubric_version", "v1"),
        schema_version=artefact.get("schema_version", 1),
        submission_id=artefact.get("submission_id", ""),
        total_score=envelope.get("total_score", 0),
    )
    return rendered


def _replay_filename(artefact: dict[str, Any], extension: str) -> str:
    """Return ``arena-replay-<short>-<ymd>.<extension>``."""
    sid = str(artefact.get("submission_id") or "unknown")
    short = sid.split("-", 1)[0]
    envelope = artefact.get("envelope") or {}
    graded_at = str(envelope.get("graded_at") or "")
    ymd = graded_at[:10].replace(":", "").replace("-", "") if graded_at else "00000000"
    return f"arena-replay-{short}-{ymd}.{extension}"


async def _build_replay_for_request(
    submission_id: uuid.UUID,
    *,
    share: str | None,
    user: User | None,
    db: AsyncSession,
) -> tuple[dict[str, Any], Submission, SessionRow]:
    """Authenticate then build — shared by both replay endpoints."""
    from app.reports.replay import build_replay
    from app.reports.verification import verify_secret as resolve_verify_secret

    submission, session, redact = await _auth_replay_view(submission_id, share, user, db)
    settings = get_settings()
    try:
        secret = resolve_verify_secret(settings)
    except RuntimeError as exc:
        logger.error("[replay] verify secret unavailable: {}", exc)
        raise HTTPException(
            status_code=503,
            detail={
                "code": "verification_secret_unavailable",
                "message": "replay service is misconfigured; try again later",
            },
        ) from exc

    artefact = await build_replay(
        db,
        submission_id,
        redact_payloads=redact,
        verify_secret_value=secret,
    )
    return artefact, submission, session


@submissions_router.get(
    "/{submission_id}/replay.json",
    summary="Canonical replay artefact as JSON (P1-6)",
    responses={
        200: {"description": "Canonical replay artefact"},
        404: {"description": "Not found / unauthorised / non-graded / tutorial"},
        503: {"description": "Verification service misconfigured"},
    },
)
async def get_replay_json(
    submission_id: uuid.UUID,
    share: str | None = Query(None, description="Optional signed share token"),
    user: User | None = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Serve the deterministic JSON artefact.

    Owners see the full artefact (including prompt payloads + the
    scratchpad body). Share-token holders see the redacted form; the
    scratchpad is omitted entirely. Anonymous callers 404.

    The response is cacheable for a year + immutable: a graded
    submission's replay is byte-identical across replays except for
    ``exported_at``, which is excluded from the signature — the ETag
    therefore pins to the signature, not the body bytes.
    """
    from app.observability import (
        replay_export_bytes,
        replay_export_errors_total,
        replay_export_requests_total,
    )

    try:
        artefact, _submission, _session = await _build_replay_for_request(
            submission_id, share=share, user=user, db=db
        )
    except HTTPException as exc:
        if exc.status_code == 404:
            replay_export_errors_total.labels("json", "not_found").inc()
        elif exc.status_code == 503:
            replay_export_errors_total.labels("json", "secret_unavailable").inc()
        else:
            replay_export_errors_total.labels("json", f"http_{exc.status_code}").inc()
        raise
    except Exception as exc:
        replay_export_errors_total.labels(
            "json", exc.__class__.__name__
        ).inc()
        logger.exception("[replay] json build failed: {}", exc)
        raise HTTPException(status_code=500, detail="replay build failed") from exc

    from app.reports.replay import canonical_json

    body = canonical_json(artefact)
    replay_export_requests_total.labels("json").inc()
    replay_export_bytes.labels("json").observe(len(body))

    # Per P1-6 audit item 15: ``Vary: Authorization, Cookie`` prevents
    # a CDN keyed only on URL from serving a cached owner response to
    # a later share-token caller of the same URL (owners see prompt
    # payloads and the scratchpad body that the share-token redaction
    # path strips). ``X-Content-Type-Options: nosniff`` blocks
    # MIME-confusion attacks against the JSON body.
    headers = {
        "Cache-Control": "public, max-age=31536000, immutable",
        "ETag": _replay_etag(artefact),
        "Vary": "Authorization, Cookie",
        "X-Content-Type-Options": "nosniff",
        "X-Robots-Tag": "noindex",
    }
    return Response(
        content=body,
        status_code=200,
        media_type="application/json",
        headers=headers,
    )


@submissions_router.get(
    "/{submission_id}/replay.zip",
    summary="Replay bundle (zip): replay.json + final.diff + verify.html + README (P1-6)",
    responses={
        200: {"description": "ZIP stream"},
        404: {"description": "Not found / unauthorised / non-graded / tutorial"},
        503: {"description": "Verification service misconfigured"},
    },
)
async def get_replay_zip(
    submission_id: uuid.UUID,
    share: str | None = Query(None, description="Optional signed share token"),
    user: User | None = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Serve the full bundle as a ZIP.

    The zip is built in memory using ``zipfile.ZipFile`` against an
    ``io.BytesIO`` buffer; bundles are bounded by the replay artefact's
    typical size (< 100 KB per design) plus the templated HTML/MD
    (~4 KB) and an optional agent_patch.diff (< 16 KB). The body is
    fully buffered before the response, so :class:`Response` (which
    computes ``Content-Length`` from ``len(body)`` automatically) is
    the correct vehicle — :class:`StreamingResponse` was misleading
    because nothing actually streams.
    """
    import io
    import zipfile

    from app.observability import (
        replay_export_bytes,
        replay_export_errors_total,
        replay_export_requests_total,
    )

    try:
        artefact, _submission, session = await _build_replay_for_request(
            submission_id, share=share, user=user, db=db
        )
    except HTTPException as exc:
        if exc.status_code == 404:
            replay_export_errors_total.labels("zip", "not_found").inc()
        elif exc.status_code == 503:
            replay_export_errors_total.labels("zip", "secret_unavailable").inc()
        else:
            replay_export_errors_total.labels("zip", f"http_{exc.status_code}").inc()
        raise
    except Exception as exc:
        replay_export_errors_total.labels(
            "zip", exc.__class__.__name__
        ).inc()
        logger.exception("[replay] zip build failed: {}", exc)
        raise HTTPException(status_code=500, detail="replay build failed") from exc

    from app.reports.replay import canonical_json

    settings = get_settings()
    canonical_url = _replay_canonical_url(settings, submission_id)
    replay_bytes = canonical_json(artefact)
    final_diff_text = artefact.get("final_diff") or ""

    # Owner-vs-share gate (P1-6 audit item 26): the mission's seeded
    # ``agent_patch.diff`` is mission-owned content; we still ship it
    # to the owner who downloaded the bundle but withhold it from
    # share-token recipients (whose bundle is the privacy-redacted
    # form). We re-walk the auth path here rather than inferring from
    # the artefact (an owner whose session has no note row would
    # mis-classify) — the second call hits the same SQLAlchemy
    # identity map and adds ~zero overhead.
    _sub_for_redact, _sess_for_redact, redact = await _auth_replay_view(
        submission_id, share, user, db
    )

    # Optional agent_patch.diff — shipped under the operator's mission
    # license, and ONLY in owner-downloaded bundles. Reads the file via
    # the mission loader's declared ``agent.patch_file`` field; absent
    # for tutorial missions (which we already 404 above) and for
    # missions whose folder is unreachable on this server.
    if redact:
        agent_patch_text = None
    else:
        agent_patch_text = _read_agent_patch_diff(
            settings.missions_root, session.mission_id
        )
    has_agent_patch = bool(agent_patch_text)

    readme_text = _render_readme(artefact, has_agent_patch=has_agent_patch)
    verify_html_text = _render_verify_html(artefact, canonical_url=canonical_url)

    buf = io.BytesIO()
    # ``compression=ZIP_DEFLATED`` keeps bundles tight; the standard
    # library implementation is pure-Python deterministic (modulo the
    # mtime, which we fix below to 1980-01-01 — the zipfile epoch).
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, content in [
            ("replay.json", replay_bytes),
            ("final.diff", final_diff_text.encode("utf-8")),
            ("README.md", readme_text.encode("utf-8")),
            ("verify.html", verify_html_text.encode("utf-8")),
        ]:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, content)
        if has_agent_patch:
            info = zipfile.ZipInfo("agent_patch.diff", date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, (agent_patch_text or "").encode("utf-8"))

    body = buf.getvalue()
    replay_export_requests_total.labels("zip").inc()
    replay_export_bytes.labels("zip").observe(len(body))

    filename = _replay_filename(artefact, "zip")
    # Per P1-6 audit item 15: ``Vary: Authorization, Cookie`` prevents
    # a CDN keyed only on URL from serving a cached owner zip (which
    # includes the scratchpad and the agent_patch.diff) to a later
    # share-token caller of the same URL. ``nosniff`` blocks
    # MIME-confusion attacks against the ZIP body.
    headers = {
        "Cache-Control": "public, max-age=31536000, immutable",
        "Content-Disposition": f'attachment; filename="{filename}"',
        "ETag": _replay_etag(artefact),
        "Vary": "Authorization, Cookie",
        "X-Content-Type-Options": "nosniff",
        "X-Robots-Tag": "noindex",
    }
    return Response(
        content=body,
        status_code=200,
        media_type="application/zip",
        headers=headers,
    )


# ---------------------------------------------------------------------------
# P1-4 — Coaching reflection endpoint
# ---------------------------------------------------------------------------
#
# Owner-only. Lazy-loaded by the FE when the post-mortem coaching
# section enters the viewport. We DELIBERATELY do not accept a
# share-token here (the coaching reflection embeds the user's private
# scratchpad text — share-token holders 403).
#
# Status surface:
#   * 401 — anonymous caller (no session cookie).
#   * 403 — caller is signed in but does not own the submission, OR
#     attached a ``?share=`` token (we explicitly refuse). The 403
#     bodies carry no detail about the underlying reason to avoid
#     fingerprinting.
#   * 404 — submission unknown / not graded.
#   * 503 ``{code: "llm_unavailable"}`` — coaching pipeline could
#     neither find a cached row nor generate a fresh one. The FE
#     translates this to "hide the section" silently.
#   * 200 — payload (reflection may still be ``null`` when the user
#     opted out or has no notes; the FE hides the section either way).


@submissions_router.get(
    "/{submission_id}/coaching",
    summary="Post-mortem coaching reflection — owner only (P1-4)",
    responses={
        200: {"description": "Coaching payload (reflection may be null)"},
        401: {"description": "Unauthenticated"},
        403: {"description": "Caller is not the submission owner"},
        404: {"description": "Submission not found / not graded"},
        503: {"description": "LLM unavailable AND no cached reflection"},
    },
)
async def get_submission_coaching(
    submission_id: uuid.UUID,
    share: str | None = Query(
        None,
        description=(
            "Reject share tokens explicitly — coaching is owner-only "
            "and surfaces the private scratchpad text."
        ),
    ),
    user: User | None = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Return the coaching reflection for ``submission_id``.

    Auth matrix (strict):
      * anonymous (no cookie) → 401
      * share token attached → 403 (we never serve coaching to share
        viewers, even if the share token would otherwise let them see
        the report)
      * cookie present but caller != owner → 403
      * cookie present + owner → 200 with body

    Body shape::

        {
          "reflection": str | null,
          "anchored_event_id": int | null,
          "anchored_note_quote": str | null,
          "cached": bool,
          "generated_at": iso8601
        }

    On opted-out / no-notes the body returns ``reflection=null`` (200);
    the FE hides the section. On total LLM failure with no cache the
    response is a 503 with the FastAPI envelope
    ``{"detail": {"code": "llm_unavailable", "message": "coaching unavailable"}}``.
    On non-LLM internal failures (DB blip, ORM crash) the response is
    a plain 500 — distinct status codes so dashboards can bucket the
    two failure modes separately.
    """
    from app.observability import coaching_errors_total
    from app.reports.coaching import (
        CoachingOutcome,
        CoachingReflectionRead,
        generate_coaching_reflection,
    )

    # Share-token holders are explicitly refused. This is the only
    # surface in the codebase that 403s on a perfectly valid share
    # token; the privacy contract is documented at length in
    # P1_DESIGN §P1-4.
    if share:
        raise HTTPException(status_code=403, detail="not your report")

    if user is None:
        raise HTTPException(status_code=401, detail="authentication required")

    _submission, session = await _fetch_submission(db, submission_id)
    if session.user_id != user.id:
        raise HTTPException(status_code=403, detail="not your report")
    if session.status != "graded":
        raise HTTPException(status_code=404, detail="submission not graded")

    settings = get_settings()
    # ``generate_coaching_reflection`` now returns a discriminated
    # :class:`CoachingResult` — no try/except wrapper needed at this
    # layer because the inner function maps every failure surface to
    # an outcome. Only an INTERNAL_FAILED outcome (or an unexpected
    # raise from a bug) yields a 500, and we deliberately let those
    # propagate so the platform error handler renders the standard
    # envelope rather than misleading the FE with "LLM unavailable".
    result = await generate_coaching_reflection(
        db, submission_id=submission_id, settings=settings
    )

    if result.outcome == CoachingOutcome.OK and result.payload is not None:
        return Response(
            status_code=200,
            media_type="application/json",
            content=result.payload.model_dump_json(),
        )

    if result.outcome in (CoachingOutcome.OPTED_OUT, CoachingOutcome.NO_NOTES):
        # 200 with a fully-null body so the FE renders nothing without
        # an error toast. The status code differentiates "intentionally
        # silent" from "model is sad" for dashboards.
        payload = CoachingReflectionRead(
            reflection=None,
            anchored_event_id=None,
            anchored_note_quote=None,
            cached=False,
            generated_at=datetime.now(UTC),
        )
        return Response(
            status_code=200,
            media_type="application/json",
            content=payload.model_dump_json(),
        )

    if result.outcome == CoachingOutcome.LLM_FAILED:
        # 503 with the typed envelope. The FE renders this as "section
        # unavailable" without a toast.
        coaching_errors_total.labels(error_class="llm_failed").inc()
        logger.warning(
            "coaching: 503 LLM_FAILED submission={} detail={}",
            submission_id,
            result.detail or "",
        )
        raise HTTPException(
            status_code=503,
            detail={
                "code": "llm_unavailable",
                "message": "coaching unavailable",
            },
        )

    # INTERNAL_FAILED — log + bump the counter, then 500 via HTTPException.
    # We deliberately do NOT 503 here; the FE distinguishes the two and a
    # DB outage masquerading as "model down" hides real platform issues.
    coaching_errors_total.labels(error_class="internal_failed").inc()
    logger.error(
        "coaching: 500 INTERNAL_FAILED submission={} detail={}",
        submission_id,
        result.detail or "",
    )
    raise HTTPException(status_code=500, detail="internal coaching failure")


__all__ = [
    "decode_share_token",
    "issue_share_token",
    "router",
    "submissions_router",
    "verify_router",
]
