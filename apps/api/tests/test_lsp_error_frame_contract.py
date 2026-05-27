"""LSPErrorFrame BE/FE wire-shape contract (P1-3).

The backend emits this frame as the only structured text frame on the
LSP WebSocket — before the proxy enters JSON-RPC pump mode. The
frontend (``apps/web/lib/lsp/client.ts``) parses it via the
``LSPErrorFrame`` TypeScript interface and switches the UI between
"show retry" and "degrade silently" branches based on the
``error`` discriminator.

This test pins three invariants:

1. The Pydantic shape serialises to JSON with exactly the keys the FE
   parser expects: ``type``, ``error``, ``language``, and an optional
   ``detail``.
2. ``type`` is always the literal string ``"lsp_error"`` (the FE
   narrows on it before treating the frame as an error indication).
3. The ``error`` discriminator round-trips every well-known
   :data:`app.sandbox.lsp.LSPErrorClass` member — the FE's TS union
   carries the same string set as ``LSPErrorClass``, so a drift here
   silently produces a frame the FE renders as an "unknown error".
"""

from __future__ import annotations

import json
from typing import get_args

from app.sandbox.lsp import LSPErrorClass
from app.schemas.lsp import LSPErrorFrame

# The canonical FE-side allowed key set. Mirrors the
# ``LSPErrorFrame`` interface in ``apps/web/lib/lsp/client.ts``.
_FE_REQUIRED_KEYS: frozenset[str] = frozenset({"type", "error", "language"})
_FE_OPTIONAL_KEYS: frozenset[str] = frozenset({"detail"})
_FE_ALLOWED_KEYS: frozenset[str] = _FE_REQUIRED_KEYS | _FE_OPTIONAL_KEYS


def _serialise(frame: LSPErrorFrame) -> dict[str, object]:
    """Round-trip the model through JSON so we exercise the wire path."""
    return json.loads(frame.model_dump_json())


def test_minimal_frame_emits_required_keys_only() -> None:
    """A frame with no ``detail`` MUST still carry the three required keys."""
    frame = LSPErrorFrame(error="binary_not_found", language="python")
    body = _serialise(frame)
    assert body["type"] == "lsp_error"
    assert body["error"] == "binary_not_found"
    assert body["language"] == "python"
    # ``detail`` is optional; pydantic emits ``None`` by default.
    assert body.get("detail") is None
    assert set(body.keys()) <= _FE_ALLOWED_KEYS


def test_frame_with_detail_emits_all_four_keys() -> None:
    frame = LSPErrorFrame(
        error="spawn_failed",
        language="typescript",
        detail="ENOENT: tsserver",
    )
    body = _serialise(frame)
    assert body == {
        "type": "lsp_error",
        "error": "spawn_failed",
        "language": "typescript",
        "detail": "ENOENT: tsserver",
    }


def test_type_field_is_always_lsp_error_literal() -> None:
    """The ``type`` discriminator is a hard literal — the FE narrows on it."""
    frame = LSPErrorFrame(error="unsupported_language", language="go")
    assert frame.type == "lsp_error"
    # The wire form also carries the literal verbatim (no model-rename
    # via alias / serialization_alias).
    body = _serialise(frame)
    assert body["type"] == "lsp_error"


def test_every_lspeerror_class_round_trips_through_wire_shape() -> None:
    """Every ``LSPErrorClass`` member must produce a valid frame.

    The BE constructs the frame via
    ``LSPErrorFrame(error=err.error_class, language=...)``; if the
    Pydantic schema ever tightens ``error`` to a Literal that doesn't
    cover the full ``LSPErrorClass`` set, the frame construction
    raises at serialise time and the FE sees a closed socket instead
    of a structured frame.
    """
    error_classes: tuple[str, ...] = get_args(LSPErrorClass)
    assert error_classes, "LSPErrorClass must be a non-empty Literal"
    for cls in error_classes:
        frame = LSPErrorFrame(error=cls, language="python")
        body = _serialise(frame)
        assert body["error"] == cls
        assert body["type"] == "lsp_error"
        assert body["language"] == "python"


def test_extras_forbidden() -> None:
    """``extra='forbid'`` keeps unknown FE-side keys out of the contract."""
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        LSPErrorFrame.model_validate(
            {
                "type": "lsp_error",
                "error": "spawn_failed",
                "language": "python",
                "unexpected": "boom",
            }
        )
