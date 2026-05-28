"""Prose layer hits the llm_cache on the second call.

Two consecutive ``generate_diagnosis`` calls with byte-identical
inputs must result in exactly one ``messages_create`` invocation —
the first call populates the cache; the second reads back the
canonical bytes.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.recommendations import prose


@dataclass
class _StubBlock:
    text: str


@dataclass
class _StubUsage:
    input_tokens: int = 12
    output_tokens: int = 18


@dataclass
class _StubResponse:
    content: list[_StubBlock]
    usage: _StubUsage


class _CountingClient:
    """Counts how many times ``messages_create`` was invoked."""

    def __init__(self, response_text: str = "polished diagnosis prose") -> None:
        self.calls = 0
        self._response_text = response_text

    async def messages_create(self, **_: object) -> _StubResponse:
        self.calls += 1
        return _StubResponse(
            content=[_StubBlock(text=self._response_text)],
            usage=_StubUsage(),
        )


@pytest.mark.asyncio
async def test_second_call_with_same_inputs_hits_cache(
    db_session, monkeypatch
) -> None:
    monkeypatch.setattr(prose, "_llm_enabled", lambda: True)
    client = _CountingClient()
    monkeypatch.setattr(prose, "_build_client", lambda: client)

    kwargs = {
        "weakest_dim": "agent_review",
        "weakest_dim_avg": 1.7,
        "recommended_mission_ids": ("goroutine-leak", "auth-cookie-expiration"),
        "weakest_dim_attempts": 4,
    }

    first = await prose.generate_diagnosis(db_session, **kwargs)
    await db_session.commit()
    second = await prose.generate_diagnosis(db_session, **kwargs)

    assert first == "polished diagnosis prose"
    assert second == "polished diagnosis prose"
    assert client.calls == 1, (
        "expected exactly one live model call across two identical lookups"
    )


@pytest.mark.asyncio
async def test_generate_why_cache_hit(db_session, monkeypatch) -> None:
    """Same chokepoint discipline for the per-mission why helper."""

    monkeypatch.setattr(prose, "_llm_enabled", lambda: True)
    client = _CountingClient(response_text="polished why prose")
    monkeypatch.setattr(prose, "_build_client", lambda: client)

    kwargs = {
        "mission_id": "goroutine-leak",
        "weakest_dim": "verification",
        "failure_mode": "goroutine_leak",
        "expected_weak_dim": "verification",
        "alignment": 1.0,
        "freshness_fresh": False,
        "mode": "normal",
    }

    first = await prose.generate_why(db_session, **kwargs)
    await db_session.commit()
    second = await prose.generate_why(db_session, **kwargs)

    assert first == "polished why prose"
    assert second == "polished why prose"
    assert client.calls == 1
