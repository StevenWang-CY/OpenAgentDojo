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

from fastapi import APIRouter, Depends, HTTPException, Query
from jose import ExpiredSignatureError, JWTError, jwt
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user, require_auth
from app.config import Settings, get_settings
from app.db.session import get_db
from app.models.session import SessionRow
from app.models.submission import Submission
from app.models.user import User
from app.schemas.auth import ShareTokenRead
from app.schemas.submission import SubmissionRead

router = APIRouter(prefix="/reports", tags=["reports"])

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
    candidates = list(missions_root.glob(f"*-{mission_id}")) + list(missions_root.glob(mission_id))
    for candidate in candidates:
        if candidate.is_dir() and (candidate / "mission.yaml").exists():
            return candidate
    return None


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
    submission: Submission, ideal_solution: str | None, status: str
) -> SubmissionRead:
    data = SubmissionRead.model_validate(submission)
    # Only render ideal_solution when the session is graded.
    rendered_ideal = ideal_solution if status == "graded" else None
    return data.model_copy(update={"ideal_solution": rendered_ideal})


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
    if share:
        try:
            share_sub = decode_share_token_strict(share, settings)
            share_ok = share_sub == submission_id
            if not share_ok:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "reason": "invalid",
                        "message": "share link is for a different submission",
                    },
                )
        except ShareTokenError as exc:
            raise HTTPException(
                status_code=400,
                detail={"reason": exc.reason, "message": str(exc)},
            ) from exc

    if not share_ok:
        if user is None:
            raise HTTPException(status_code=401, detail="authentication required")
        if session.user_id != user.id:
            raise HTTPException(status_code=403, detail="not your report")

    ideal = _read_ideal_solution(settings.missions_root, session.mission_id)
    return _to_read_model(submission, ideal, session.status)


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


__all__ = ["decode_share_token", "issue_share_token", "router"]
