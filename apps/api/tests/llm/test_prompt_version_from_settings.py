"""The LLM cache key's ``prompt_version`` must reflect the operator-configured
``PROMPT_VERSION`` knob, not the stale import-time module constant.

pydantic-settings loads ``PROMPT_VERSION`` from ``.env`` into
``Settings.prompt_version`` but NEVER into ``os.environ``. The module-level
``app.llm.domains.PROMPT_VERSION`` constant reads the env directly at import,
so once the process is up, bumping the documented knob can't change it — and
because the production callsites key the ``llm_cache`` row on
``(domain, content_hash, prompt_version)``, a bump that's meant to invalidate
every cached row after a prompt-template edit would silently fail, serving
stale LLM output.

``get_prompt_version()`` resolves the canonical ``Settings.prompt_version``
field instead, so the configured value threads into the cache key. These
tests pin that contract end-to-end.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

import app.config as config_module
from app.config import Settings
from app.llm import domains
from app.llm.cache import GeneratedOutput
from app.recommendations import prose


def _settings_with_prompt_version(monkeypatch: pytest.MonkeyPatch, version: int) -> Settings:
    """Build a fresh ``Settings`` honouring ``PROMPT_VERSION=version`` and
    install it as the cached singleton ``get_prompt_version`` resolves.

    This mirrors what pydantic-settings does at boot: ``PROMPT_VERSION`` lands
    in ``Settings.prompt_version`` — NOT in ``os.environ`` for the module
    constant to pick up. We override ``app.config.get_settings`` (the lazy
    import target inside ``get_prompt_version``) so the resolved value is the
    configured one.
    """
    monkeypatch.setenv("PROMPT_VERSION", str(version))
    configured = Settings()
    assert configured.prompt_version == version
    monkeypatch.setattr(config_module, "get_settings", lambda: configured)
    return configured


def test_get_prompt_version_resolves_configured_settings_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``get_prompt_version()`` returns the configured value, not the stale
    import-time constant.

    Fails before the fix: the function returned ``domains.PROMPT_VERSION``
    (pinned to 1 because the env wasn't set at import time), so the assert
    ``== 2`` would see ``1``.
    """
    _settings_with_prompt_version(monkeypatch, 2)

    # The import-time constant is unchanged — it read the env at import,
    # before this test set PROMPT_VERSION. That's exactly the trap.
    assert domains.PROMPT_VERSION == 1

    assert domains.get_prompt_version() == 2


def test_get_prompt_version_falls_back_when_settings_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Settings failure must not become a new crash surface — fall back to
    the import-time constant rather than propagating."""

    def _boom() -> Settings:
        raise RuntimeError("settings unavailable")

    monkeypatch.setattr(config_module, "get_settings", _boom)

    assert domains.get_prompt_version() == domains.PROMPT_VERSION


@dataclass
class _StubBlock:
    text: str


@dataclass
class _StubUsage:
    input_tokens: int = 7
    output_tokens: int = 9


@dataclass
class _StubResponse:
    content: list[_StubBlock]
    usage: _StubUsage


class _FakeClient:
    async def messages_create(self, **_: object) -> _StubResponse:
        return _StubResponse(
            content=[_StubBlock(text="polished prose")],
            usage=_StubUsage(),
        )


@pytest.mark.asyncio
async def test_configured_prompt_version_threads_into_diagnosis_cache_key(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The recommendation-prose callsite keys the cache row on the configured
    ``prompt_version``.

    Fails before the fix: the callsite imported the module constant
    ``PROMPT_VERSION`` (1) directly, so the captured kwarg was ``1`` even
    though the operator configured ``2`` — the bump never invalidated the
    cache.
    """
    _settings_with_prompt_version(monkeypatch, 2)
    monkeypatch.setattr(prose, "_llm_enabled", lambda: True)
    monkeypatch.setattr(prose, "_build_client", lambda: _FakeClient())

    captured: dict[str, Any] = {}

    async def _fake_get_or_generate(_db, **kwargs: Any):
        captured.update(kwargs)
        gen = kwargs["generator"]
        result = await gen()
        return GeneratedOutput(
            output=result.output,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )

    monkeypatch.setattr(prose, "get_or_generate", _fake_get_or_generate)

    await prose.generate_diagnosis(
        db_session,
        weakest_dim="agent_review",
        weakest_dim_avg=1.7,
        recommended_mission_ids=("goroutine-leak",),
        weakest_dim_attempts=3,
    )

    assert captured["prompt_version"] == 2
