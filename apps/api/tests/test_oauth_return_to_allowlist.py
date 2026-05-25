"""Phase 4.A.T5 — strict allowlist on the OAuth + magic-link ``return_to``.

The historical "starts with /" guard let too much through:
``/api/...``, ``/auth/sign-in?error=...``, backslash escapes, URL-
encoded slashes. The Phase 4.A.7 allowlist replaces it with a small
set of FE-route regexes (missions, workspace, report, verify, profile,
account, skills, root).

This test exercises the pure ``_validate_return_to`` function for the
common cases.
"""

from __future__ import annotations

import uuid

import pytest

from app.auth.github_oauth import _validate_return_to
from app.config import get_settings


@pytest.mark.parametrize(
    "raw",
    [
        # The old "starts with /" guard accepted these — must now reject.
        "//evil.example.com/path",
        "/api/v1/admin/users",
        "/auth/sign-in?error=spoofed",
        "/foo/bar",
        # Backslash + URL-encoded slash escapes.
        "/missions\\..\\admin",
        "/account%2f..%2fadmin",
        "/account%5C..%5Cadmin",
        # CR/LF/TAB header-splitting payloads.
        "/missions\r\nLocation: http://evil",
        "/missions\nfoo",
        "/missions\tfoo",
        # Not a relative path.
        "https://evil.example.com",
        "missions",  # no leading slash
        "",
        None,
    ],
)
def test_return_to_rejected(raw) -> None:
    assert _validate_return_to(raw, get_settings()) is None, (
        f"return_to={raw!r} should have been rejected"
    )


def test_return_to_accepts_missions_root() -> None:
    assert _validate_return_to("/missions", get_settings()) == "/missions"


def test_return_to_accepts_mission_slug() -> None:
    assert _validate_return_to("/missions/01-tutorial", get_settings()) == "/missions/01-tutorial"


def test_return_to_accepts_report_uuid() -> None:
    rid = str(uuid.uuid4())
    assert _validate_return_to(f"/report/{rid}", get_settings()) == f"/report/{rid}"


def test_return_to_accepts_workspace_uuid() -> None:
    wid = str(uuid.uuid4())
    assert _validate_return_to(f"/workspace/{wid}", get_settings()) == f"/workspace/{wid}"


def test_return_to_accepts_root() -> None:
    assert _validate_return_to("/", get_settings()) == "/"


def test_return_to_accepts_account_tab() -> None:
    assert _validate_return_to("/account/email", get_settings()) == "/account/email"


def test_return_to_accepts_skills() -> None:
    assert _validate_return_to("/skills", get_settings()) == "/skills"


def test_return_to_rejects_oversized() -> None:
    assert _validate_return_to("/missions/" + "a" * 300, get_settings()) is None
