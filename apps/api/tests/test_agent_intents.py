"""Unit tests for the deterministic intent classifier."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from app.agent.intents import (
    DEFAULT_INTENT_MAP,
    IntentClassifier,
    IntentMap,
    classify,
    clear_intent_cache,
    load_intents,
)


@pytest.fixture(autouse=True)
def _reset_caches() -> None:
    clear_intent_cache()
    yield
    clear_intent_cache()


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("please fix the auth bug", "fix"),
        ("repair the broken middleware", "fix"),
        ("debug the failing case", "fix"),
        ("investigate why /dashboard 500s", "fix"),
        ("add a regression test for expired cookies", "test"),
        ("cover the expiration code path", "test"),
        ("we need to assert the redirect", "test"),
        ("revise the previous diff", "revise"),
        ("try again, but smaller", "revise"),
        ("redo the change", "revise"),
        ("narrow the scope to middleware only", "narrow"),
        ("only change requireAuth.ts", "narrow"),
        ("what does this codebase do?", "unknown"),
        ("", "unknown"),
    ],
)
def test_classify_with_default_map(prompt: str, expected: str) -> None:
    assert classify(prompt, DEFAULT_INTENT_MAP) == expected


def test_classify_fix_beats_narrow_when_both_match() -> None:
    """Priority order is canonical: ``fix`` > ``narrow``."""
    prompt = "please fix the bug with a minimal narrow diff"
    assert classify(prompt, DEFAULT_INTENT_MAP) == "fix"


def test_load_intents_uses_default_when_no_intents_file(tmp_path: Path) -> None:
    """Mission with no ``agent.intents_file`` falls back to default keywords."""
    manifest = SimpleNamespace(
        id="m1",
        agent=SimpleNamespace(intents_file=None),
    )
    mission = SimpleNamespace(folder=tmp_path, manifest=manifest)
    intents = load_intents(mission)
    assert intents is DEFAULT_INTENT_MAP
    assert IntentClassifier(intents)("fix the bug") == "fix"


def test_load_intents_reads_yaml_and_caches(tmp_path: Path) -> None:
    """Loaded intents file is parsed and re-used on the second call."""
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "intents.yaml").write_text(
        yaml.safe_dump(
            {
                "intents": {
                    "fix": {"keywords": ["squash", "patch"]},
                    "test": ["coverage"],
                    "revise": {"keywords": ["redo"]},
                    "narrow": {"keywords": ["only"]},
                }
            }
        ),
        encoding="utf-8",
    )
    manifest = SimpleNamespace(
        id="cached-mission",
        agent=SimpleNamespace(intents_file="prompts/intents.yaml"),
    )
    mission = SimpleNamespace(folder=tmp_path, manifest=manifest)

    first = load_intents(mission)
    assert isinstance(first, IntentMap)
    assert first.source_sha256 != ""
    classifier = IntentClassifier(first)
    assert classifier("please squash this bug") == "fix"
    assert classifier("add coverage for X") == "test"
    assert classifier("redo it") == "revise"
    assert classifier("only the middleware") == "narrow"
    assert classifier("noop") == "unknown"

    # Second load returns the cached object (identity equality).
    second = load_intents(mission)
    assert second is first


def test_load_intents_handles_missing_file(tmp_path: Path) -> None:
    """A declared-but-missing intents file falls back to default."""
    manifest = SimpleNamespace(
        id="m2",
        agent=SimpleNamespace(intents_file="prompts/intents.yaml"),
    )
    mission = SimpleNamespace(folder=tmp_path, manifest=manifest)
    assert load_intents(mission) is DEFAULT_INTENT_MAP


def test_classifier_for_mission_uses_default_when_folder_missing() -> None:
    """When the mission has no folder reference at all we use defaults."""
    manifest = SimpleNamespace(agent=SimpleNamespace(intents_file="prompts/intents.yaml"))
    assert IntentClassifier.for_mission(manifest).intent_map is DEFAULT_INTENT_MAP
