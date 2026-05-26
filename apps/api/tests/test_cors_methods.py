"""Regression test: CORS allow_methods must cover every HTTP verb the API
actually exposes. If a route uses a verb that's missing from the CORS
allow-list, the browser preflight returns 400 and the FE surfaces the
failure as a generic "Network error contacting <url>", which is hard to
diagnose. Surface the drift at test time instead.
"""

from __future__ import annotations

from fastapi.routing import APIRoute

from app.main import app


def _route_methods() -> set[str]:
    methods: set[str] = set()
    for route in app.routes:
        if isinstance(route, APIRoute):
            # ``HEAD`` is implicitly added by FastAPI for every GET and is
            # safelisted by browsers, so it doesn't need to be in
            # allow_methods explicitly.
            methods.update(m for m in route.methods if m != "HEAD")
    return methods


def _cors_allow_methods() -> set[str]:
    for middleware in app.user_middleware:
        if middleware.cls.__name__ == "CORSMiddleware":
            allowed = middleware.kwargs.get("allow_methods", [])
            return {m.upper() for m in allowed}
    raise AssertionError("CORSMiddleware not installed")


def test_cors_allow_methods_covers_every_declared_route_verb() -> None:
    declared = _route_methods()
    allowed = _cors_allow_methods()
    missing = declared - allowed
    assert not missing, (
        f"CORS allow_methods missing verbs used by APIRoutes: {sorted(missing)}. "
        "Add them to ``allow_methods`` in apps/api/app/main.py — otherwise the "
        "browser preflight will 400 and the frontend will show a generic "
        '"Network error contacting <url>" toast.'
    )


def test_cors_allow_methods_includes_options_for_preflight() -> None:
    # Preflight depends on the server replying to OPTIONS even though no route
    # explicitly declares it.
    assert "OPTIONS" in _cors_allow_methods()
