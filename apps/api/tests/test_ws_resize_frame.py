"""Terminal WS recognises the binary resize control frame.

The frame layout (coordinated with the 4.5 frontend) is:

    [0x01, cols_hi, cols_lo, rows_hi, rows_lo]   # 5 bytes

When the writer task sees that exact shape it issues a ``TIOCSWINSZ`` ioctl
on the PTY instead of forwarding the bytes verbatim. Any other payload (or a
short payload) is treated as regular keystrokes.
"""

from __future__ import annotations

import struct
from unittest.mock import MagicMock

from app.ws import terminal as terminal_mod


def test_resize_frame_triggers_ioctl(monkeypatch) -> None:
    calls: list[tuple[int, int, bytes]] = []

    def _fake_ioctl(fd: int, op: int, arg: bytes) -> bytes:
        calls.append((fd, op, arg))
        return arg

    monkeypatch.setattr(terminal_mod.fcntl, "ioctl", _fake_ioctl)

    # cols=160, rows=42
    frame = bytes([0x01]) + struct.pack(">H", 160) + struct.pack(">H", 42)
    consumed = terminal_mod._apply_resize(99, frame)

    assert consumed is True
    assert len(calls) == 1
    fd, op, arg = calls[0]
    assert fd == 99
    assert op == terminal_mod.termios.TIOCSWINSZ
    rows, cols, _x, _y = struct.unpack("HHHH", arg)
    assert rows == 42
    assert cols == 160


def test_regular_bytes_are_not_consumed_by_resize() -> None:
    # Plain keystrokes (e.g. "ls\n") MUST be passed through to the PTY.
    consumed = terminal_mod._apply_resize(99, b"ls\n")
    assert consumed is False


def test_resize_short_frame_is_not_consumed() -> None:
    # 4-byte payload starting with 0x01 should not be mistaken for a resize.
    consumed = terminal_mod._apply_resize(99, bytes([0x01, 0x00, 0x10, 0x00]))
    assert consumed is False


def test_resize_zero_dims_noops_but_consumes(monkeypatch) -> None:
    monkeypatch.setattr(
        terminal_mod.fcntl,
        "ioctl",
        MagicMock(side_effect=AssertionError("ioctl should not be called on 0x0 resize")),
    )
    frame = bytes([0x01, 0x00, 0x00, 0x00, 0x00])
    assert terminal_mod._apply_resize(99, frame) is True
