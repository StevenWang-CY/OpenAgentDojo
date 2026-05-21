"""Terminal WS control-frame parser (P0-B9).

The FE periodically sends a ``{"type":"ping"}`` keep-alive. Without server-side
recognition those bytes would land on the PTY and corrupt the shell prompt.
"""

from __future__ import annotations

from app.ws.terminal import _is_control_message


def test_ping_frame_is_recognised() -> None:
    msg = _is_control_message(b'{"type":"ping"}')
    assert msg is not None
    assert msg["type"] == "ping"


def test_pong_frame_is_recognised() -> None:
    msg = _is_control_message(b'{"type":"pong","ts":123}')
    assert msg is not None
    assert msg["type"] == "pong"


def test_regular_shell_input_is_not_control() -> None:
    # Plain `ls` keystrokes should not be misclassified.
    assert _is_control_message(b"ls\n") is None
    assert _is_control_message(b"echo hello") is None


def test_oversized_frames_pass_through_as_shell_input() -> None:
    """A JSON object > 256 bytes is treated as PTY input, not a control msg.

    This is a defence-in-depth bound to keep the parser cheap and avoid
    accidentally classifying a user's pasted JSON as control data.
    """
    big = b'{"type":"ping","junk":"' + b"x" * 1024 + b'"}'
    assert _is_control_message(big) is None


def test_arbitrary_json_with_no_type_is_passed_through() -> None:
    """An object without a string `type` is not a control frame."""
    assert _is_control_message(b'{"foo":"bar"}') is None


def test_non_json_bytes_pass_through() -> None:
    assert _is_control_message(b"\x01\x02\x03") is None
    assert _is_control_message(b"") is None
