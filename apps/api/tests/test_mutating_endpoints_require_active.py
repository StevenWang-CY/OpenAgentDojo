"""Mutating workspace endpoints must reject when the session is not active.

The sandbox-handle 503 is a defence in depth; the primary guard is the
session.status check. During the brief ``submitting`` window the handle
still exists and a stray ``file.edited`` / ``command.run`` /
``apply_patch`` would mutate the workspace mid-grade and the submitted
diff would no longer match the on-disk repo.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException

from app.sessions.router import _require_mutable_session


class _FakeRow:
    def __init__(self, status: str) -> None:
        self.status = status
        self.id = uuid.uuid4()


@pytest.mark.parametrize(
    "bad_status", ["submitting", "graded", "abandoned", "error", "provisioning"]
)
def test_require_mutable_rejects_non_active(bad_status: str) -> None:
    row = _FakeRow(bad_status)
    with pytest.raises(HTTPException) as exc_info:
        _require_mutable_session(row)
    err = exc_info.value
    assert err.status_code == 409
    assert isinstance(err.detail, dict)
    assert err.detail["code"] == "session_not_active"
    assert err.detail["session_status"] == bad_status


def test_require_mutable_passes_for_active() -> None:
    row = _FakeRow("active")
    # Must not raise.
    _require_mutable_session(row)
