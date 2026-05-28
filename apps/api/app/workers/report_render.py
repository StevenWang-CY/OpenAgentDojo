"""Render a verification PDF or PNG for a submission (P0-11).

Entry point :func:`render_report` is the RQ task target. It runs in the
worker process (or inline when ``get_queue()`` returns None — see the
reports route's in-process fallback) and produces a print-fidelity PDF
or a LinkedIn-sized PNG of the report, uploads it to the artifact
bucket, and updates the ``report_renders`` row to ``ready``.

Render strategy
---------------
The worker visits an internal Next.js route at
``/report-print/{submission_id}?token=<HMAC>`` which renders the full
report in a print-friendly layout. We use Playwright Chromium so the
PDF is pixel-faithful to the live page (per design §P0-11). When
Playwright is not available in the worker process — local dev without
the browser binary installed, or a stripped CI image — the worker
flips the row to ``failed`` with a clear error instead of raising; the
FE surfaces the failure as a "PDF generation unavailable" toast.

Determinism + identity
----------------------
The S3 key is ``report-renders/{submission_id}/{kind}.{ext}``. A force
re-render reuses the same key — the bucket overwrites in place — so a
recruiter's link to a previously-rendered PDF starts pointing at the
fresh content the moment the worker uploads. The signature stays
constant; only the underlying bytes change.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from loguru import logger
from sqlalchemy import select

from app.config import get_settings
from app.models.report_render import (
    RENDER_KIND_PDF,
    RENDER_STATUS_FAILED,
    RENDER_STATUS_QUEUED,
    RENDER_STATUS_READY,
    RENDER_STATUS_RUNNING,
    ReportRender,
)
from app.reports.verification import verify_secret
from app.storage import put_object


# Marker so the worker can degrade gracefully when the browser dep is
# missing — the route still returns 202 and the FE handles "failed"
# without crashing the request.
class _PlaywrightUnavailable(RuntimeError):
    pass


def make_render_token(submission_id: uuid.UUID, render_id: uuid.UUID, secret: str) -> str:
    """HMAC-SHA256 over ``submission_id`` (and the render_id for log
    correlation) — the API's ``/reports/{id}/print`` route validates the
    submission-id-only HMAC, so the render_id is included in the
    payload only as audit-trail metadata.

    The Next.js ``/report-print`` route forwards this header to the
    backend so a public request can't stumble onto the unstyled
    internal render surface.
    """
    # render_id reserved for future audit hooks — eagerly silence the
    # 'unused' warning without spending a second HMAC call.
    _ = render_id
    return hmac.new(
        secret.encode("utf-8"),
        str(submission_id).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _render_s3_key(submission_id: uuid.UUID, kind: str) -> str:
    ext = "pdf" if kind == RENDER_KIND_PDF else "png"
    return f"report-renders/{submission_id}/{kind}.{ext}"


def _content_type_for_kind(kind: str) -> str:
    return "application/pdf" if kind == RENDER_KIND_PDF else "image/png"


def render_report(render_id_str: str, *, inline: bool = False) -> None:
    """RQ entry point — synchronous wrapper around the async pipeline.

    ``inline=True`` is set by the route's in-process fallback (no RQ
    available). In that mode failures are NOT re-raised — the row state
    is the contract and the route has already returned 202.
    """
    try:
        asyncio.run(_async_render(uuid.UUID(render_id_str), inline=inline))
    except _PlaywrightUnavailable as exc:
        # Worker can't run the browser — keep going (the row was already
        # marked failed inside the async path). RQ would otherwise retry
        # against the same broken environment.
        logger.warning("report_render: Playwright unavailable: {}", exc)
        if not inline:
            return
    except Exception as exc:  # pragma: no cover — defensive
        logger.exception("report_render: unhandled failure for {}: {}", render_id_str, exc)
        if not inline:
            raise


async def _async_render(render_id: uuid.UUID, *, inline: bool) -> None:
    from app.db.session import AsyncSessionLocal

    settings = get_settings()

    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(select(ReportRender).where(ReportRender.id == render_id))
        ).scalar_one_or_none()
        if row is None:
            logger.warning("report_render: row {} disappeared before render", render_id)
            return

        # Phase 4.A.9 — idempotent skip on terminal states. RQ can
        # re-deliver a job whose acknowledgement got lost, and the
        # in-process fallback path can fire a stale task if the route
        # re-enqueued after a worker crashed mid-flight. Re-running
        # against a row that's already ``ready`` (or ``failed``) would
        # either overwrite a known-good artefact OR flip a failed row
        # back to ``running`` and bury the original error. The SETNX
        # window between ``queued`` and ``running`` still allows a
        # transient collision between two workers; that's accepted
        # (both upload the same bytes against the same S3 key — the
        # second upload is a no-op overwrite).
        if row.status in (RENDER_STATUS_READY, RENDER_STATUS_FAILED):
            logger.info(
                "report_render: idempotent skip for render={} status={} (already terminal)",
                render_id,
                row.status,
            )
            return

        row.status = RENDER_STATUS_RUNNING
        await db.commit()

        secret = verify_secret(settings)
        token = make_render_token(row.submission_id, row.id, secret)

        web_origin = (settings.web_origin or "http://localhost:3000").rstrip("/")
        target_url = f"{web_origin}/report-print/{row.submission_id}?token={token}&kind={row.kind}"
        # Derive the host shown in the PDF footer from the same origin
        # the worker visits so a deployment on (say) ``arena.acme.io``
        # produces "verified via arena.acme.io" rather than the
        # hard-coded production hostname. Falls back to the canonical
        # public host if the origin is malformed.
        host_label = urlparse(web_origin).netloc or "openagentdojo.app"

        try:
            payload = await asyncio.to_thread(
                _render_via_playwright, target_url, row.kind, token, host_label
            )
        except _PlaywrightUnavailable as exc:
            await _mark_failed(db, row, f"playwright unavailable: {exc}")
            if not inline:
                raise
            return
        except Exception as exc:
            await _mark_failed(db, row, f"render error: {exc}")
            if not inline:
                raise
            return

        try:
            s3_key = _render_s3_key(row.submission_id, row.kind)
            await asyncio.to_thread(
                put_object, s3_key, payload, content_type=_content_type_for_kind(row.kind)
            )
        except Exception as exc:
            await _mark_failed(db, row, f"upload failed: {exc}")
            if not inline:
                raise
            return

        now = datetime.now(UTC)
        row.status = RENDER_STATUS_READY
        row.s3_key = s3_key
        row.bytes = len(payload)
        row.ready_at = now
        row.error = None
        await db.commit()
        logger.info(
            "report_render: render {} ready for submission {} ({} bytes)",
            row.id,
            row.submission_id,
            len(payload),
        )


async def _mark_failed(db: Any, row: ReportRender, error: str) -> None:
    row.status = RENDER_STATUS_FAILED
    row.error = error[:500]
    await db.commit()


# ---------------------------------------------------------------------------
# Stuck-row sweeper (P1-4)
# ---------------------------------------------------------------------------


async def sweep_stuck_renders(
    db: Any,
    *,
    stale_after_s: int = 300,
    queued_stale_after_s: int = 60,
) -> int:
    """Recover ``report_renders`` rows abandoned by the worker pipeline.

    Returns the total number of rows the sweep touched (rescued queued +
    flipped running). Two failure modes covered:

    1. **Orphaned ``queued`` row** — the route enqueued into RQ but no
       worker consumed the job (queue had no live consumer, worker
       crashed between enqueue and dequeue, or the route fell through
       the inline path and the background task was cancelled before it
       could run). Without this branch the FE polls a row that never
       moves; the user's force-rerender budget is consumed for nothing.
       The sweep schedules an inline render for any queued row older
       than ``queued_stale_after_s`` (default 60 s — long enough to
       avoid racing a healthy worker, short enough that the user
       doesn't sit waiting).

    2. **Wedged ``running`` row** — worker SIGTERM'd mid-render, OOM
       killed, Playwright deadlock, or the upload to S3 hung. Any
       ``running`` row whose ``created_at`` is older than
       ``stale_after_s`` is dead by every plausible definition and gets
       flipped to ``failed``. The error column gives operators a
       distinct signature to alert on.

    Idempotent + race-safe: the worker's own
    ``RENDER_STATUS_READY/FAILED`` early-return guard at
    ``_async_render`` line 136 prevents double work if the sweep races
    a worker that's about to finish.
    """
    from datetime import timedelta

    now = datetime.now(UTC)
    queued_horizon = now - timedelta(seconds=int(max(1, queued_stale_after_s)))
    running_horizon = now - timedelta(seconds=int(max(1, stale_after_s)))

    # 1. Rescue queued rows by scheduling them inline. Fire-and-forget
    # via asyncio.create_task — same dispatch pattern the route uses for
    # its no-Redis fallback. The task updates the row out-of-band; the
    # sweep doesn't await completion (a long render could block the
    # entire sweep loop otherwise).
    queued_rows = (
        (
            await db.execute(
                select(ReportRender).where(
                    ReportRender.status == RENDER_STATUS_QUEUED,
                    ReportRender.created_at < queued_horizon,
                )
            )
        )
        .scalars()
        .all()
    )
    rescued = 0
    if queued_rows:
        import asyncio as _asyncio

        for row in queued_rows:
            task = _asyncio.create_task(_async_render(row.id, inline=True))
            # Anchor the task so the GC doesn't finalise it mid-flight.
            _SWEEP_RESCUE_TASKS.add(task)
            task.add_done_callback(_SWEEP_RESCUE_TASKS.discard)
            rescued += 1
        logger.info(
            "report_render sweep: rescued {} stuck queued row(s) via inline render",
            rescued,
        )

    # 2. Flip wedged running rows to failed.
    running_rows = (
        (
            await db.execute(
                select(ReportRender).where(
                    ReportRender.status == RENDER_STATUS_RUNNING,
                    ReportRender.created_at < running_horizon,
                )
            )
        )
        .scalars()
        .all()
    )
    if not running_rows:
        return rescued
    for row in running_rows:
        row.status = RENDER_STATUS_FAILED
        row.error = "render_timed_out_after_shutdown"
    await db.commit()
    logger.warning(
        "report_render sweep: flipped {} stuck running row(s) to failed",
        len(running_rows),
    )
    return rescued + len(running_rows)


# Anchor for inline rescue tasks spawned by sweep_stuck_renders. The
# discard callback runs when each task completes.
_SWEEP_RESCUE_TASKS: set[Any] = set()


# ---------------------------------------------------------------------------
# Playwright bridge
# ---------------------------------------------------------------------------


def _render_via_playwright(
    target_url: str,
    kind: str,
    token: str,
    host_label: str = "openagentdojo.app",
) -> bytes:
    """Synchronous Playwright bridge — runs on a thread per asyncio.to_thread.

    Importing Playwright at module top would pin the dep on every API
    process even though only the worker needs it. We import lazily and
    raise :class:`_PlaywrightUnavailable` when the binary or the Python
    package is missing — the worker captures this and flips the row to
    ``failed`` with a useful error.

    ``host_label`` is the hostname embedded in the PDF footer (e.g.
    ``"openagentdojo.app"``). Defaults to the canonical public host so
    callers in tests / scripts that don't supply one still produce a
    sensible footer.
    """
    try:
        from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]
    except ImportError as exc:
        raise _PlaywrightUnavailable(
            "playwright python package is not installed; install via "
            "'uv pip install playwright && playwright install chromium' in the worker image"
        ) from exc

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                ctx = browser.new_context(
                    viewport={"width": 1200, "height": 630} if kind == "png" else None,
                    extra_http_headers={"X-Render-Token": token},
                )
                page = ctx.new_page()
                page.goto(target_url, wait_until="networkidle", timeout=60_000)
                if kind == RENDER_KIND_PDF:
                    footer_template = (
                        "<div style='font-size:8px;color:#666;width:100%;"
                        "text-align:center;'>"
                        f"verified via {host_label} — "
                        "<span class='pageNumber'></span> / "
                        "<span class='totalPages'></span>"
                        "</div>"
                    )
                    pdf_bytes: bytes = page.pdf(
                        format="Letter",
                        print_background=True,
                        margin={"top": "16mm", "bottom": "16mm", "left": "16mm", "right": "16mm"},
                        display_header_footer=True,
                        header_template="<div></div>",
                        footer_template=footer_template,
                    )
                    return pdf_bytes
                # PNG path.
                png_bytes: bytes = page.screenshot(type="png", full_page=False)
                return png_bytes
            finally:
                browser.close()
    except _PlaywrightUnavailable:
        raise
    except Exception as exc:
        # Surface upstream — the worker logs + flips the row to failed.
        raise RuntimeError(f"playwright render failed: {exc}") from exc


__all__ = ["_async_render", "make_render_token", "render_report"]
