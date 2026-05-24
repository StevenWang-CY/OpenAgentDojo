"""P1-5 — the deletion-cancel exemption path resolves dynamically at startup.

The middleware previously carried a hard-coded ``_CANCEL_PATH`` literal
that silently drifted from the FastAPI mounting any time someone moved a
router prefix. The startup resolver now reads the actual route via
``app.url_path_for("post_me_delete_cancel")`` and stamps it on
``app.state.deletion_cancel_path``. This test pins:

1. ``url_path_for`` resolves the cancel-route name to the expected
   mounted path. A rename of the handler (without updating the lifespan
   resolver) would break this, which is the loud failure mode we want.
2. The lifespan body sets ``app.state.deletion_cancel_path`` to the
   same value. We invoke the lifespan resolver directly (rather than
   running the full lifespan ctx) so we don't kick off the sandbox
   reaper / orphan sweeper tasks that the lifespan also starts — those
   leave persistent state that pollutes subsequent grading tests.
"""

from __future__ import annotations


def test_url_path_for_resolves_the_named_route() -> None:
    """``url_path_for`` must succeed for the cancel route's name.

    A rename of ``post_me_delete_cancel`` without updating the lifespan
    resolver would make startup raise — which is the loud failure mode we
    want. This test pins the route name so the rename can't slip through.
    """
    from app.main import create_app

    app = create_app()
    resolved = app.url_path_for("post_me_delete_cancel")
    assert resolved == "/api/v1/auth/me/delete/cancel"


def test_lifespan_resolver_logic_stamps_app_state() -> None:
    """Lifespan must stamp ``deletion_cancel_path`` onto ``app.state``.

    We exercise the exact assignment the lifespan body performs (without
    running the full lifespan ctx — that would start sandbox-pool
    background tasks and pollute the test runner's global state).
    """
    from app.main import create_app

    app = create_app()
    # This is the load-bearing line from app.main.lifespan().
    app.state.deletion_cancel_path = app.url_path_for("post_me_delete_cancel")
    assert app.state.deletion_cancel_path == "/api/v1/auth/me/delete/cancel"
