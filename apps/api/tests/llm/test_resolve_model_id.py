"""resolve_model_id delegates to the existing app.agent.llm resolver.

The substrate must NOT carry its own logical-to-inference-profile map.
This test pins that ``app.llm.client.resolve_model_id`` returns
exactly what ``app.agent.llm.resolve_anthropic_model_id`` returns for
the same input.
"""

from __future__ import annotations

import pytest

from app.agent.llm import resolve_anthropic_model_id
from app.llm.client import resolve_model_id


@pytest.mark.parametrize(
    "logical_id",
    ["claude-haiku-4-5", "claude-sonnet-4-6", "claude-opus-4-7"],
)
def test_resolve_model_id_matches_agent_resolver(logical_id: str) -> None:
    assert resolve_model_id(logical_id) == resolve_anthropic_model_id(logical_id)


def test_resolve_model_id_passes_through_unknown_ids() -> None:
    # Civitas resolver returns input unchanged for unknown ids (and the
    # no-civitas fallback does too); the substrate must mirror that.
    assert resolve_model_id("nonexistent-model") == resolve_anthropic_model_id(
        "nonexistent-model"
    )
