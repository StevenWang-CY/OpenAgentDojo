"""GitHub OAuth provider (P0-7).

End-to-end primitives for the github.com/login/oauth/* round-trip:

  * :func:`build_authorize_url` — assembles the URL the FE redirects to.
  * :func:`issue_oauth_state` / :func:`consume_oauth_state` — short-lived
    JWT bound to a cookie. Protects against CSRF on the callback and lets
    a logged-out user pass a ``return_to`` hint through GitHub without
    handing the FE the chance to forge it.
  * :func:`exchange_code_for_token` — POSTs the auth code to GitHub and
    returns the bearer access token.
  * :func:`fetch_user_profile` — GETs ``/user`` + ``/user/emails`` and
    returns a normalised :class:`GithubProfile`.

The routes layer wires these together and owns the DB upsert. This module
deliberately stays a pure protocol layer — no DB imports, no Pydantic
state machine — so the route handler is the only place ``users`` rows
are mutated.
"""

from __future__ import annotations

import re
import secrets
import urllib.parse
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import httpx
from fastapi import HTTPException, Request, Response
from jose import JWTError, jwt
from loguru import logger

from app.config import Settings
from app.schemas.auth import GithubProfile

# OAuth state cookie. Short-lived (10 min), HttpOnly so JS cannot read it,
# SameSite=Lax so the cookie travels with the GitHub → callback redirect
# (GitHub.com → our callback is a top-level navigation, which Lax permits).
OAUTH_STATE_COOKIE_NAME: Final[str] = "arena_oauth_state"
OAUTH_STATE_TTL_SECONDS: Final[int] = 10 * 60
_OAUTH_STATE_ALGORITHM: Final[str] = "HS256"

# GitHub endpoints. Hard-coded — these have been stable since 2016 and
# substituting them per-deploy would just open a malicious-redirect hole
# (the only legitimate way to point at a different IdP is to fork the
# module and add a new provider). Build-step URLs live here; route URLs
# (``/auth/github/start`` etc.) live on the router.
_GITHUB_AUTHORIZE_URL: Final[str] = "https://github.com/login/oauth/authorize"
# ruff's S105 ("password in code") fires on any constant containing the
# substring "token"; this is the URL of the OAuth token-exchange endpoint,
# not a credential — ignore at the assignment line below.
_GITHUB_TOKEN_URL: Final[str] = "https://github.com/login/oauth/access_token"  # noqa: S105
_GITHUB_API_USER: Final[str] = "https://api.github.com/user"
_GITHUB_API_USER_EMAILS: Final[str] = "https://api.github.com/user/emails"

# OAuth scopes. ``read:user`` exposes the public profile (login, name, id,
# avatar_url, html_url); ``user:email`` is needed to read the user's
# *verified* primary email (which we use as the upsert key). We deliberately
# do NOT request ``repo`` or any write scope — this is identity-only.
_GITHUB_SCOPES: Final[str] = "read:user user:email"

# HTTP timeout for all GitHub round-trips. 10s is generous (GitHub
# typically responds in <300ms) but bounded so a wedged TCP connection
# can't pin the request event loop forever.
_HTTP_TIMEOUT_SECONDS: Final[float] = 10.0


class GithubOAuthError(Exception):
    """Typed failure surfaced to the route layer.

    Carries a short ``code`` the route uses to differentiate user-visible
    redirects (``code=github_oauth_failed``) from operator log lines. The
    ``message`` is logged but never echoed back to the caller — GitHub
    error strings can leak internal IDs.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class OAuthStateReplayError(GithubOAuthError):
    """A previously-consumed state nonce was re-presented (Phase 4.A.6).

    Raised by :func:`consume_oauth_state` when the Redis-backed
    single-use guard rejects the nonce. The callback route translates
    this to the standard ``/auth/sign-in?error=github_oauth_failed``
    redirect — same UX as any other state-mismatch failure. Distinct
    type lets dashboards page on replay attempts specifically.
    """

    def __init__(self, message: str = "oauth state nonce replayed") -> None:
        super().__init__("oauth_state_replayed", message)


# ---------------------------------------------------------------------------
# Authorize URL + state token
# ---------------------------------------------------------------------------


def _default_redirect_uri(settings: Settings) -> str:
    """Compute the OAuth redirect URI from ``web_origin`` when unset.

    The FE's ``/auth/github/callback`` page proxies to the API's
    ``/api/v1/auth/github/callback``; we point GitHub at the API path
    directly so the cookie set on the response lands on the API origin
    (where every other auth cookie lives).
    """
    explicit = (settings.github_oauth_redirect_uri or "").strip()
    if explicit:
        return explicit
    # Fallback: same host as the web origin, /api/v1/auth/github/callback.
    # This works for the dev deployment where API + web share the host;
    # split-host deployments MUST set GITHUB_OAUTH_REDIRECT_URI explicitly.
    web_origin = (settings.web_origin or "http://localhost:3000").rstrip("/")
    return f"{web_origin}/api/v1/auth/github/callback"


def build_authorize_url(state: str, settings: Settings) -> str:
    """Return the GitHub authorize URL the FE should redirect to.

    ``state`` is the opaque JWT minted by :func:`issue_oauth_state` and
    persisted as a cookie. GitHub echoes it back as ``?state=…`` on the
    callback; :func:`consume_oauth_state` verifies it matches the cookie
    AND that the JWT verifies and isn't expired.
    """
    client_id = (settings.github_oauth_client_id or "").strip()
    if not client_id:
        raise GithubOAuthError(
            "oauth_unavailable",
            "github oauth client id is not configured",
        )
    params = {
        "client_id": client_id,
        "redirect_uri": _default_redirect_uri(settings),
        "scope": _GITHUB_SCOPES,
        "state": state,
        # ``allow_signup=true`` lets a brand-new GitHub user sign up on
        # github.com mid-flow and continue back to us. We never want to
        # block that — the worst case is "user creates github account,
        # then we get the verified email".
        "allow_signup": "true",
    }
    return f"{_GITHUB_AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


# Phase 4.A.7 — strict allowlist of FE routes the OAuth callback (and the
# P0-10 magic-link callback) may redirect to. Each pattern matches a
# single concrete in-app path the FE actually renders today. Adding a new
# legal target is a deliberate edit here; an attacker-controlled
# ``return_to`` that doesn't match any of these is dropped (the caller
# falls back to ``/missions``).
#
# Patterns:
#   * ``/missions`` + ``/missions/<slug>``
#   * ``/workspace/<uuid>``       — live session
#   * ``/report/<uuid>``          — graded session
#   * ``/verify/<uuid>``          — public verification page
#   * ``/profile/<handle>``       — public profile
#   * ``/account`` + ``/account/<tab>`` — settings hub
#   * ``/skills``                 — skill index
#   * ``/``                       — home
#
# The patterns are intentionally tighter than the historical "starts with
# a single slash" guard: that admitted ``/api/...`` (a same-host API
# path, which would render an empty body), ``/auth/sign-in?error=...``
# (lets a phishing payload spoof the sign-in chrome), and any unknown
# FE route — all of which are now rejected.
_RETURN_TO_ALLOWLIST: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"^/$"),
    re.compile(r"^/missions(/[a-z0-9-]+)?$"),
    re.compile(r"^/workspace/[0-9a-f-]{36}$"),
    re.compile(r"^/report/[0-9a-f-]{36}$"),
    re.compile(r"^/verify/[0-9a-f-]{36}$"),
    re.compile(r"^/profile/[a-z0-9_-]+$"),
    re.compile(r"^/account(/[a-z]+)?$"),
    re.compile(r"^/skills$"),
)

# Characters that must NOT appear anywhere in a return_to. Backslash, URL-
# encoded slashes, CR/LF/TAB — any of which could let a tainted value
# slip past the allowlist regex (or split a header on the redirect line).
# The check runs BEFORE the allowlist so the regex itself never sees the
# attack characters.
_RETURN_TO_FORBIDDEN_SUBSTRINGS: Final[tuple[str, ...]] = (
    "\\",
    "%2f",
    "%2F",
    "%5c",
    "%5C",
    "\r",
    "\n",
    "\t",
)


def _validate_return_to(return_to: str | None, settings: Settings) -> str | None:
    """Reject ``return_to`` values that aren't on the FE route allowlist.

    Phase 4.A.7 hardened this from a "starts with /" check to a strict
    allowlist of FE paths the OAuth + magic-link callbacks may redirect
    to. Anything else (including ``/api/...``, ``/auth/sign-in?error=...``,
    backslash escapes, URL-encoded slashes, CR/LF) is dropped silently;
    the callback falls back to ``/missions``.

    The 256-char length bound is preserved as defence-in-depth against a
    JWT-bloating attack that tries to push the state cookie above the 4KB
    browser limit. ``settings`` is accepted for forward-compat with a
    future deployment-specific allowlist.
    """
    _ = settings
    if not return_to:
        return None
    if len(return_to) > 256:
        return None
    if not return_to.startswith("/"):
        return None
    # Reject protocol-relative URLs (``//evil.example/...``) and
    # backslash / URL-encoded escapes that could bypass the allowlist
    # regex by smuggling a second slash through the unescape step.
    for needle in _RETURN_TO_FORBIDDEN_SUBSTRINGS:
        if needle in return_to:
            return None
    if return_to.startswith("//"):
        return None
    # Finally, the path must match one of the FE-route patterns above.
    # We strip the query string before matching so ``/account?tab=email``
    # is rejected (the allowlist is path-only — query-bearing redirects
    # are a phishing surface we don't need today).
    path_only = return_to.split("?", 1)[0].split("#", 1)[0]
    for pattern in _RETURN_TO_ALLOWLIST:
        if pattern.match(path_only):
            return path_only
    return None


def issue_oauth_state(response: Response, settings: Settings, *, return_to: str | None) -> str:
    """Mint a state JWT, attach it as ``arena_oauth_state`` cookie, return value.

    The JWT carries:

      * ``nonce`` — 16 random bytes (urlsafe). The callback compares the
        JWT's nonce to the cookie's JWT nonce; tampering on the URL
        invalidates the match.
      * ``iat`` / ``exp`` — 10-minute window. Cookies that survive a
        forgotten browser tab still expire server-side.
      * ``return_to`` — optional relative path the callback redirects to
        after minting the session cookie. Validated to be a same-origin
        relative path (no ``//`` protocol-relative escape).
    """
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "nonce": secrets.token_urlsafe(16),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=OAUTH_STATE_TTL_SECONDS)).timestamp()),
    }
    validated_return_to = _validate_return_to(return_to, settings)
    if validated_return_to is not None:
        payload["return_to"] = validated_return_to
    token: str = jwt.encode(payload, settings.session_secret, algorithm=_OAUTH_STATE_ALGORITHM)
    response.set_cookie(
        key=OAUTH_STATE_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=OAUTH_STATE_TTL_SECONDS,
        path="/",
    )
    return token


# Phase 4.A.6 — single-use Redis key prefix. The full key is
# ``auth:oauth_state_nonce:{nonce}`` with the same TTL as the state
# cookie (10 min). SETNX semantics: the first consume succeeds and
# stores the marker; any subsequent consume of the same nonce raises
# :class:`OAuthStateReplayError`. Redis being unavailable degrades to
# "allow consume" with a warning — the cookie / JWT checks above are
# still the primary CSRF defence; single-use is defence-in-depth.
_OAUTH_STATE_NONCE_KEY_PREFIX: Final[str] = "auth:oauth_state_nonce:"


async def _claim_state_nonce_single_use(nonce: str) -> bool:
    """Return True iff this is the FIRST consume of ``nonce``.

    SETNX a key with the state cookie's TTL so the key naturally
    expires alongside any uncollected state. Returns ``True`` on first
    write, ``False`` on replay. Returns ``True`` (best-effort allow) when
    Redis is unavailable so a flaky cache can't lock all users out of
    OAuth sign-in — the cookie / JWT checks remain the primary defence.
    """
    from app.sessions.events import get_redis

    try:
        redis = await get_redis()
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning(
            "oauth state nonce defence: redis lookup raised, allowing consume: {}",
            exc,
        )
        return True
    if redis is None:
        logger.warning(
            "oauth state nonce defence: redis unavailable, allowing consume "
            "(single-use is best-effort defence-in-depth)"
        )
        return True
    key = _OAUTH_STATE_NONCE_KEY_PREFIX + nonce
    try:
        # ``nx=True`` makes the SET behave like SETNX; redis-py returns
        # ``True`` when the key was written and ``None`` / ``False``
        # when the key already existed.
        result = await redis.set(key, "1", ex=OAUTH_STATE_TTL_SECONDS, nx=True)
    except Exception as exc:
        logger.warning(
            "oauth state nonce defence: redis SETNX raised, allowing consume: {}",
            exc,
        )
        return True
    return bool(result)


async def consume_oauth_state(
    request: Request, settings: Settings, *, presented_state: str
) -> dict[str, Any]:
    """Verify the cookie + presented state agree, return the JWT payload.

    Four independent checks must pass:

    1. The cookie must be present (proves the browser is the same one
       that initiated ``/auth/github/start``).
    2. The cookie value MUST equal the ``state`` GitHub echoed back on
       the callback URL. A mismatch is a CSRF or replay attempt.
    3. The JWT must verify against ``session_secret`` and not be expired.
    4. Phase 4.A.6 — the JWT's ``nonce`` MUST be unused. We SETNX a key
       ``auth:oauth_state_nonce:{nonce}`` with a 10-min TTL; a hit on a
       previously-consumed nonce raises :class:`OAuthStateReplayError`.
       The route translates this to the standard
       ``/auth/sign-in?error=github_oauth_failed`` redirect. Falls back
       to "allow" when Redis is unavailable — the cookie + JWT checks
       are the primary CSRF defence; single-use is defence-in-depth.

    Raises :class:`fastapi.HTTPException` 400 on cookie / JWT failure or
    :class:`OAuthStateReplayError` on nonce replay — the route handler
    converts either into a redirect to
    ``web_origin/auth/sign-in?error=github_oauth_failed`` so the user sees
    a clean error page instead of a raw 400.
    """
    cookie_value = request.cookies.get(OAUTH_STATE_COOKIE_NAME)
    if not cookie_value:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "oauth_state_missing",
                "message": "oauth state cookie missing",
            },
        )
    if cookie_value != presented_state:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "oauth_state_mismatch",
                "message": "oauth state cookie does not match callback state",
            },
        )
    try:
        payload: dict[str, Any] = jwt.decode(
            cookie_value,
            settings.session_secret,
            algorithms=[_OAUTH_STATE_ALGORITHM],
        )
    except JWTError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "oauth_state_invalid",
                "message": "oauth state cookie failed JWT verification",
            },
        ) from exc

    # Phase 4.A.6 — single-use nonce enforcement. The nonce was minted in
    # ``issue_oauth_state`` and lives only inside the JWT payload (not on
    # any database row); the Redis SETNX is the only single-use anchor.
    nonce = payload.get("nonce")
    if isinstance(nonce, str) and nonce:
        first_use = await _claim_state_nonce_single_use(nonce)
        if not first_use:
            raise OAuthStateReplayError(
                f"oauth state nonce already consumed (nonce_prefix={nonce[:8]})"
            )
    return payload


# ---------------------------------------------------------------------------
# Token + profile fetch
# ---------------------------------------------------------------------------


async def exchange_code_for_token(code: str, settings: Settings) -> str:
    """Exchange the GitHub authorization code for an OAuth access token.

    Posts to ``https://github.com/login/oauth/access_token`` with
    ``Accept: application/json`` so the response is JSON (GitHub defaults
    to form-encoded otherwise). Raises :class:`GithubOAuthError` on
    non-200 responses, JSON parse failures, or missing ``access_token``
    keys.
    """
    client_id = (settings.github_oauth_client_id or "").strip()
    client_secret = (settings.github_oauth_client_secret or "").strip()
    if not client_id or not client_secret:
        raise GithubOAuthError("oauth_unavailable", "github oauth credentials not configured")

    body = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "redirect_uri": _default_redirect_uri(settings),
    }
    headers = {"Accept": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS) as client:
            resp = await client.post(_GITHUB_TOKEN_URL, json=body, headers=headers)
    except httpx.HTTPError as exc:
        logger.warning("github oauth token exchange transport error: {}", exc)
        raise GithubOAuthError(
            "oauth_transport_error",
            f"network error contacting github: {exc}",
        ) from exc

    if resp.status_code != 200:
        logger.warning(
            "github oauth token exchange non-200 status={} body_len={}",
            resp.status_code,
            len(resp.text or ""),
        )
        raise GithubOAuthError(
            "oauth_token_exchange_failed",
            f"github returned status {resp.status_code}",
        )

    try:
        payload = resp.json()
    except ValueError as exc:
        logger.warning("github oauth token exchange non-json body")
        raise GithubOAuthError(
            "oauth_token_exchange_failed", "github returned non-json body"
        ) from exc
    if not isinstance(payload, dict) or "access_token" not in payload:
        # GitHub returns ``{"error": "...", "error_description": "..."}`` on
        # bad codes. Log the error code (NOT the description — it can echo
        # the raw code back) so ops can correlate.
        error_code = payload.get("error") if isinstance(payload, dict) else "unknown"
        logger.warning("github oauth token exchange returned error code={}", error_code)
        raise GithubOAuthError(
            "oauth_token_exchange_failed",
            f"github error: {error_code}",
        )
    access_token = payload["access_token"]
    if not isinstance(access_token, str) or not access_token:
        raise GithubOAuthError("oauth_token_exchange_failed", "github returned empty access token")
    return access_token


async def fetch_user_profile(access_token: str) -> GithubProfile:
    """Return the GitHub user + their primary verified email.

    Two round-trips:

    1. ``GET /user`` — returns ``{id, login, name, avatar_url, html_url, ...}``.
    2. ``GET /user/emails`` — list of email objects with
       ``{email, primary, verified}``. We pick the entry where
       ``primary == True AND verified == True``. If no such row exists,
       :class:`GithubOAuthError` is raised — we refuse to attach an
       unverified email to a local account.
    """
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "OpenAgentDojo",
    }
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS) as client:
            user_resp = await client.get(_GITHUB_API_USER, headers=headers)
            emails_resp = await client.get(_GITHUB_API_USER_EMAILS, headers=headers)
    except httpx.HTTPError as exc:
        logger.warning("github oauth user fetch transport error: {}", exc)
        raise GithubOAuthError(
            "oauth_transport_error",
            f"network error contacting github user api: {exc}",
        ) from exc

    if user_resp.status_code != 200:
        logger.warning("github oauth user fetch non-200 status={}", user_resp.status_code)
        raise GithubOAuthError(
            "oauth_user_fetch_failed",
            f"github /user returned status {user_resp.status_code}",
        )
    if emails_resp.status_code != 200:
        logger.warning(
            "github oauth emails fetch non-200 status={}",
            emails_resp.status_code,
        )
        raise GithubOAuthError(
            "oauth_user_fetch_failed",
            f"github /user/emails returned status {emails_resp.status_code}",
        )

    try:
        user_payload = user_resp.json()
        emails_payload = emails_resp.json()
    except ValueError as exc:
        raise GithubOAuthError("oauth_user_fetch_failed", "github returned non-json body") from exc

    if not isinstance(user_payload, dict):
        raise GithubOAuthError("oauth_user_fetch_failed", "github /user returned non-dict body")
    if not isinstance(emails_payload, list):
        raise GithubOAuthError(
            "oauth_user_fetch_failed",
            "github /user/emails returned non-list body",
        )

    github_id = user_payload.get("id")
    login = user_payload.get("login")
    html_url = user_payload.get("html_url")
    if (
        not isinstance(github_id, int)
        or not isinstance(login, str)
        or not isinstance(html_url, str)
    ):
        raise GithubOAuthError(
            "oauth_user_fetch_failed",
            "github /user missing required fields (id/login/html_url)",
        )

    primary_email: str | None = None
    for entry in emails_payload:
        if not isinstance(entry, dict):
            continue
        if entry.get("primary") is True and entry.get("verified") is True:
            candidate = entry.get("email")
            if isinstance(candidate, str) and candidate:
                primary_email = candidate.strip().lower()
                break
    if not primary_email:
        # No verified primary email — refuse rather than guess. The user
        # can either verify their email on github.com and retry, or fall
        # back to the magic-link sign-in flow.
        logger.warning(
            "github oauth: no verified primary email for github_id={}",
            github_id,
        )
        raise GithubOAuthError(
            "oauth_email_unverified",
            "github account has no verified primary email",
        )

    name_raw = user_payload.get("name")
    name = name_raw if isinstance(name_raw, str) and name_raw else None
    avatar_raw = user_payload.get("avatar_url")
    avatar_url = avatar_raw if isinstance(avatar_raw, str) and avatar_raw else None

    return GithubProfile(
        github_id=github_id,
        login=login,
        name=name,
        avatar_url=avatar_url,
        html_url=html_url,
        email=primary_email,
    )


__all__ = [
    "OAUTH_STATE_COOKIE_NAME",
    "OAUTH_STATE_TTL_SECONDS",
    "GithubOAuthError",
    "OAuthStateReplayError",
    "build_authorize_url",
    "consume_oauth_state",
    "exchange_code_for_token",
    "fetch_user_profile",
    "issue_oauth_state",
]
