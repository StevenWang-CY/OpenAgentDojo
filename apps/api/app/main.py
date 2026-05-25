"""FastAPI application entrypoint.

Wires routers, middleware, observability, and lifecycle hooks. The app is
constructed via :func:`create_app` so tests can build isolated instances.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from loguru import logger
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app import __version__
from app.agent.router import router as agent_router
from app.auth.routes import router as auth_router
from app.config import Settings, get_settings
from app.healthz import router as health_router
from app.middleware import (
    BannedCommandsMiddleware,
    CSRFMiddleware,
    DeletionLockMiddleware,
    RateLimitMiddleware,
    RequestIdMiddleware,
    SecurityHeadersMiddleware,
)
from app.missions.router import router as missions_router
from app.observability import configure_logging, metrics_asgi_app
from app.profiles.router import router as profiles_router
from app.reports.router import router as reports_router
from app.reports.router import verify_router
from app.sandbox.pool import SandboxPool
from app.sessions.integrity import router as integrity_router
from app.sessions.router import router as sessions_router
from app.status.router import api_v1_router as status_v1_router
from app.status.router import router as status_router


class ArenaError(Exception):
    """Base class for application-level errors with a stable error code."""

    def __init__(self, message: str, code: str = "internal_error", status_code: int = 500):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


# P1-4 — periodic sweep for ``report_renders`` rows pinned at ``running``
# past the worker's job timeout. Spawned by the lifespan handler.
_RENDER_SWEEP_INTERVAL_S = 60
_RENDER_SWEEP_STALE_AFTER_S = 300


async def _render_sweep_loop() -> None:
    """Periodically flip stuck ``report_renders.status='running'`` rows to
    ``failed`` so a wedged worker doesn't pin a row forever.

    The loop opens a fresh ``AsyncSession`` per iteration so a transient
    DB error in one sweep doesn't poison a long-lived session. Errors
    are logged and swallowed — the worker recovers on the next tick;
    the loop exits cleanly on ``CancelledError`` (the lifespan path).
    """
    from app.db.session import AsyncSessionLocal
    from app.workers.report_render import sweep_stuck_renders

    while True:
        try:
            await asyncio.sleep(_RENDER_SWEEP_INTERVAL_S)
        except asyncio.CancelledError:
            return
        try:
            async with AsyncSessionLocal() as db:
                await sweep_stuck_renders(db, stale_after_s=_RENDER_SWEEP_STALE_AFTER_S)
        except asyncio.CancelledError:
            return
        except Exception as exc:  # sweep must keep ticking past per-iteration errors
            logger.warning("report_render_sweep_iteration_failed: {}", exc)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = get_settings()
    configure_logging(settings.log_level)

    # Boot timestamp — read by the public ``/status`` route to compute uptime.
    # Set on app.state so create_app() can be invoked multiple times in tests
    # and each instance gets its own uptime clock.
    app.state.boot_at = time.time()

    # P1-5 — resolve the deletion-cancel route's effective path NOW so the
    # middleware never has to fall back to a hard-coded literal that could
    # drift from a router prefix change. ``url_path_for`` raises if the
    # named route is missing, which is the loud failure mode we want at
    # boot rather than a silent self-403 at runtime.
    app.state.deletion_cancel_path = app.url_path_for("post_me_delete_cancel")

    # P2 bundle — warn (don't refuse) when dev env has an HTTPS web_origin.
    # Almost always a misconfigured deploy that left ARENA_ENV=development
    # while pointing at a real domain — cookies stay non-secure, the dev
    # IP-keyed auth fallback may be live, and the validator wouldn't fire
    # because it only enforces in staging/production. Surface it loudly.
    if settings.arena_env == "development" and (settings.web_origin or "").lower().startswith(
        "https://"
    ):
        logger.warning(
            "config sanity: ARENA_ENV=development but WEB_ORIGIN={!r} starts with https://. "
            "This usually means a deploy left ARENA_ENV unset. Double-check.",
            settings.web_origin,
        )

    # Startup log — never include secrets. Provider is logged only when
    # configured so that misconfiguration is visible at boot.
    provider = settings.llm_provider
    if provider == "bedrock":
        logger.info("provider=bedrock region={}", settings.aws_region)
    elif provider == "direct":
        logger.info("provider=direct (anthropic api key present)")
    else:
        logger.info("provider=disabled (no LLM credentials configured)")

    logger.info(
        "agentarena api booting — version={} env={} sandbox_driver={}",
        __version__,
        settings.arena_env,
        settings.sandbox_driver,
    )

    if settings.sandbox_driver == "local":
        logger.warning(
            "SANDBOX_DRIVER=local — no isolation, dev only. Do NOT enable this in production."
        )

    # Construct sandbox pool + background tasks (idempotent for the local driver).
    # The reaper kills idle sandboxes; the orphan sweeper marks crashed
    # ``active`` DB rows (those with no live pool handle) as ``abandoned`` so
    # they don't leak forever after an API crash. See §M8 plan.
    pool = SandboxPool(settings=settings)
    app.state.sandbox_pool = pool
    reaper_task = asyncio.create_task(pool.reaper_loop(), name="sandbox-reaper")
    orphan_task = asyncio.create_task(pool.orphan_sweeper_loop(), name="sandbox-orphan-sweeper")

    # P1-4 — periodic sweep for ``report_renders`` rows pinned at
    # ``running`` past the worker's own job timeout (e.g. worker SIGTERM
    # during shutdown, OOM kill, Playwright wedged). Without this, the
    # row hangs forever, the FE poll never resolves, and the user's
    # force-rerender budget is silently consumed.
    render_sweep_task = asyncio.create_task(_render_sweep_loop(), name="report-render-sweep")

    # Let the in-process provision fallback see the running app so it can
    # register handles on the pool (the WS terminal route reads from it).
    from app.workers.provision import register_app

    register_app(app)

    try:
        yield
    finally:
        # Drain any in-flight background grading tasks first so they get
        # a chance to commit their submission row + verification envelope
        # before the thread pool that backs ``driver.read_file`` shims
        # tears down. Bounded so a wedged grader can't block shutdown.
        from app.grading.runner import (
            await_active_grading_tasks,
            shutdown_fs_executor,
        )

        completed, timed_out = await await_active_grading_tasks(deadline_seconds=10.0)
        if completed or timed_out:
            logger.info(
                "shutdown: drained grading tasks completed={} timed_out={}",
                completed,
                timed_out,
            )
        for task in (reaper_task, orphan_task, render_sweep_task):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                # Cooperative cancellation is the expected shutdown path
                # for these loops — not a signal worth shouting about.
                pass
            except Exception:
                # Anything else (a bug in the loop body) is worth a
                # structured signal so on-call has something to chase
                # without it stopping shutdown.
                logger.warning(
                    "shutdown_task_swallowed task={}",
                    task.get_name(),
                    exc_info=True,
                )
        await pool.shutdown()
        # Tear down the grading FS thread pool last — by this point no
        # validator should still be running. ``wait=True`` blocks until
        # any in-flight ``driver.read_file`` shim returns, which is the
        # belt to the suspenders of the grading-task drain above.
        shutdown_fs_executor(wait=True)
        # Close the cached Redis client so we don't leak a socket past
        # process shutdown. Best-effort — never block shutdown on it.
        try:
            from app.sessions.events import close_redis

            await close_redis()
        except Exception as exc:  # pragma: no cover — shutdown must not raise
            logger.debug("close_redis failed during shutdown (ignored): {}", exc)
        logger.info("agentarena api shutdown complete")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="OpenAgentDojo API",
        version=__version__,
        docs_url="/docs",
        redoc_url=None,
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # Middleware is applied in REVERSE order of registration in Starlette
    # (last added = outermost). Order we want, outermost first:
    #   1. RequestId       — assign correlation id BEFORE any other layer so
    #                        rejected requests (CSRF/CORS/host) still log it
    #   2. TrustedHost     — drop requests with bogus Host headers early
    #   3. SecurityHeaders — attach headers to every response (incl. errors)
    #   4. CORS            — preflight/Origin handling
    #   5. RateLimit       — per-route token bucket
    #   6. CSRF            — double-submit cookie check on unsafe methods
    #   7. DeletionLock    — P0-6: 403 mutating requests while account is
    #                        scheduled for deletion (except cancel)
    #   8. BannedCommands  — body inspection for /commands
    app.add_middleware(BannedCommandsMiddleware)
    # DeletionLock runs INSIDE CSRF (so forged requests are dropped first
    # and can't fingerprint deletion-scheduled accounts via the 403 body).
    app.add_middleware(DeletionLockMiddleware)
    app.add_middleware(CSRFMiddleware)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-CSRF-Token"],
        # Browsers hide every non-safelisted response header from cross-origin
        # JS reads unless they're listed here. ``Retry-After`` powers the
        # rate-limit "try again in Ns" UX; ``X-Request-ID`` lets the FE
        # surface a correlation id in error toasts.
        expose_headers=["Retry-After", "X-Request-ID"],
    )
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=settings.allowed_hosts_list,
    )
    # Registered LAST → executes FIRST. Every log line below this layer will
    # carry ``extra.request_id`` and every response — including 400/403 from
    # the host / CSRF / CORS guards — will echo ``X-Request-ID``.
    app.add_middleware(RequestIdMiddleware)

    # ---- error handler ----
    @app.exception_handler(ArenaError)
    async def _arena_error_handler(_: Request, exc: ArenaError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": str(exc), "code": exc.code},
        )

    @app.exception_handler(Exception)
    async def _fallback_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.opt(exception=exc).error(
            "unhandled error at {} {}", request.method, request.url.path
        )
        return JSONResponse(
            status_code=500,
            content={"detail": "internal server error", "code": "internal_error"},
        )

    # ---- routers ----
    app.include_router(health_router)  # /healthz at root
    app.include_router(status_router)  # /status at root (public; no /api/v1 prefix)
    app.include_router(status_v1_router, prefix="/api/v1")  # /api/v1/status alias
    app.include_router(missions_router, prefix="/api/v1")
    app.include_router(sessions_router, prefix="/api/v1")
    # P0-8 — integrity endpoint lives in its own module so the test suite
    # can import it without dragging the full sessions router graph along.
    # Both routers share the ``/sessions`` prefix; FastAPI merges them by
    # path, so this adds the single ``POST /events/integrity`` route.
    app.include_router(integrity_router, prefix="/api/v1")
    app.include_router(auth_router, prefix="/api/v1")
    # ``/api/v1/me`` previously aliased ``/api/v1/auth/me`` via ``me_router``;
    # we dropped the alias because the FE (apps/web/lib/api.ts ``auth.me``)
    # only calls ``/auth/me`` and shipping two paths confused the contract
    # generator. ``auth_router`` already exposes ``/auth/me``.
    app.include_router(agent_router, prefix="/api/v1")
    app.include_router(reports_router, prefix="/api/v1")
    # P0-11 — public verify surface lives at /api/v1/verify/{id} so the
    # canonical credential URL is short and doesn't pun on /reports.
    app.include_router(verify_router, prefix="/api/v1")
    app.include_router(profiles_router, prefix="/api/v1")

    # WebSocket routers — imported here (not at module top) so optional deps
    # like docker don't break unit tests that don't exercise WS code paths.
    from app.ws.events import router as events_ws_router
    from app.ws.terminal import router as terminal_ws_router

    app.include_router(terminal_ws_router)
    app.include_router(events_ws_router)

    # ---- prometheus ----
    app.mount("/metrics", metrics_asgi_app())

    # Phase 4.A.14 — stamp the cancel route's effective path on the app
    # at construction time so middleware always sees a real value
    # (lifespan is not invoked under ASGITransport-based tests; the
    # lifespan re-stamps the same value at production boot). Fails loud
    # if the named route is missing.
    app.state.deletion_cancel_path = app.url_path_for("post_me_delete_cancel")

    return app


app = create_app()
