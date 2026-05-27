"""Validator coverage for the P1-1 mission tag vocabulary.

Three guarantees:

* Every shipped mission carries a known-vocabulary failure-mode tag.
* The failure-mode tag matches ``failure_mode.id`` on the manifest.
* Tutorial missions are exempt — they may ship empty tags because
  the orientation surface lives outside the catalog grid.
* Unknown tag values raise at parse time, with a clear message that
  surfaces the offending value (not a generic "invalid").
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from app.config import get_settings
from app.missions.loader import MissionLoader
from app.missions.manifest import (
    _FAILURE_MODE_TAGS,
    _KNOWN_TAGS,
    MissionConfigError,
    MissionManifest,
)


def _load_sample_manifest_dict() -> dict[str, Any]:
    """Build a minimal valid manifest dict in code (no fs fixture needed).

    Keeps the negative-path tests independent of the on-disk fixture so
    failures here point at the validator, not at the fixture wiring.
    """
    return {
        "id": "sample-mission",
        "version": 1,
        "title": "Sample",
        "short_description": "Short.",
        "difficulty": "beginner",
        "category": "testing",
        "estimated_minutes": 10,
        "skills_tested": ["testing"],
        "repo": {
            "pack": "fullstack-auth-demo",
            "initial_commit": "abc123de",
            "workdir": "/workspace",
            "language_runtime": "node20",
            "setup_commands": [],
            "ready_check": "true",
            "test_commands": {"unit": "true"},
        },
        "brief": "A short brief.",
        "failure_mode": {
            "id": "overfitted_visible_test",
            "title": "Test failure mode",
            "description": "desc",
        },
        "expected_files": ["src/index.ts"],
        "expected_context": {
            "required": ["src/a.ts", "src/b.ts"],
            "recommended": ["src/c.ts"],
            "discouraged": ["src/d.ts"],
        },
        "agent": {
            "patch_file": "agent_patch.diff",
            "response_template": "response.md",
            "applies_when": {
                "prompt_min_chars": 40,
                "prompt_must_contain_any": ["fix"],
            },
            "apply_mode": "on_user_confirm",
        },
        "visible_tests": ["sample test"],
        "hidden_tests": {"command": "true", "expected_pass": ["sample"]},
        "validators": [
            {"kind": "diff_scope", "max_files_changed": 4, "max_added_lines": 100}
        ],
        "scoring_weights": {
            "final_correctness": 30,
            "verification": 15,
            "agent_review": 15,
            "prompt_quality": 10,
            "context_selection": 10,
            "safety": 10,
            "diff_minimality": 10,
        },
        "reward_signals": {
            "prompt_quality": {
                "must_include_any": ["reproduce", "root cause", "regression"],
                "bonus_keywords": ["security"],
                "penalty_if_under_chars": 40,
            },
            "verification": {"required_categories": ["test", "typecheck"]},
        },
        "expected_diff_lines_p50": 18,
        "published": True,
        "tags": ["overfitted_visible_test", "lang:typescript"],
        # P1-2 — every standard sample manifest must declare the
        # dimension it primarily exercises so the test fixtures
        # represent a fully-valid manifest. Set to ``safety`` to mirror
        # the canonical calibration backfill applied to shipped missions.
        "expected_weak_dim": "safety",
    }


def test_every_shipped_mission_has_known_failure_mode_tag() -> None:
    """Every standard mission carries one closed-vocab failure-mode tag."""
    get_settings.cache_clear()
    root = get_settings().missions_root
    loader = MissionLoader(root)
    loaded = loader.scan()
    assert loaded, "loader returned zero missions; check MISSIONS_ROOT"

    for m in loaded:
        manifest = m.manifest
        if manifest.kind == "tutorial":
            # Orientation is exempt; the strip lives outside the catalog grid.
            continue
        failure_tags = [t for t in manifest.tags if t in _FAILURE_MODE_TAGS]
        assert len(failure_tags) == 1, (
            f"mission {manifest.id!r} must carry exactly one failure-mode tag "
            f"(found {failure_tags!r}); see _KNOWN_TAGS in manifest.py"
        )
        assert failure_tags[0] == manifest.failure_mode.id, (
            f"mission {manifest.id!r} failure-mode tag {failure_tags[0]!r} "
            f"does not match failure_mode.id {manifest.failure_mode.id!r}"
        )
        # Every tag must be from the closed vocabulary.
        assert all(t in _KNOWN_TAGS for t in manifest.tags), (
            f"mission {manifest.id!r} carries tags outside the closed vocabulary"
        )


def test_tutorial_allows_empty_tags() -> None:
    """Tutorial missions may ship with an empty tag list."""
    data = _load_sample_manifest_dict()
    data["kind"] = "tutorial"
    # Tutorial missions ship with all-zero weights (validated separately).
    data["scoring_weights"] = dict.fromkeys(data["scoring_weights"], 0)
    data["published"] = False
    data["tags"] = []
    manifest = MissionManifest.model_validate(data)
    assert manifest.tags == []
    assert manifest.kind == "tutorial"


def test_standard_mission_without_failure_mode_tag_rejected() -> None:
    data = _load_sample_manifest_dict()
    data["tags"] = ["lang:typescript"]
    with pytest.raises((MissionConfigError, ValidationError)) as exc:
        MissionManifest.model_validate(data)
    assert "failure-mode tag" in str(exc.value)


def test_failure_mode_tag_must_match_manifest_id() -> None:
    data = _load_sample_manifest_dict()
    # Tag and failure_mode.id deliberately disagree.
    data["failure_mode"]["id"] = "race_condition"
    data["tags"] = ["overfitted_visible_test", "lang:typescript"]
    with pytest.raises((MissionConfigError, ValidationError)) as exc:
        MissionManifest.model_validate(data)
    assert "does not match" in str(exc.value)


def test_unknown_tag_raises_with_clear_message() -> None:
    data = _load_sample_manifest_dict()
    data["tags"] = ["overfitted_visible_test", "lang:elvish"]
    with pytest.raises(ValidationError) as exc:
        MissionManifest.model_validate(data)
    msg = str(exc.value)
    assert "lang:elvish" in msg
    assert "unknown" in msg.lower() or "allowed" in msg.lower()


def test_multiple_failure_mode_tags_rejected() -> None:
    """Two failure-mode tags is ambiguous; the validator surfaces both."""
    data = _load_sample_manifest_dict()
    data["tags"] = [
        "overfitted_visible_test",
        "race_condition",
        "lang:typescript",
    ]
    with pytest.raises((MissionConfigError, ValidationError)) as exc:
        MissionManifest.model_validate(data)
    assert "multiple failure-mode tags" in str(exc.value)


def test_duplicate_tags_rejected() -> None:
    data = _load_sample_manifest_dict()
    data["tags"] = ["overfitted_visible_test", "overfitted_visible_test"]
    with pytest.raises(ValidationError) as exc:
        MissionManifest.model_validate(data)
    assert "duplicate" in str(exc.value).lower()


def test_tag_cap_enforced() -> None:
    """The 8-tag cap keeps the catalog filter readable."""
    data = _load_sample_manifest_dict()
    # Build a 9-tag list mixing one failure-mode tag with 8 skill/language tags.
    data["tags"] = [
        "overfitted_visible_test",
        "skill:concurrency",
        "skill:typing",
        "skill:auth",
        "skill:http",
        "skill:sql",
        "skill:cli",
        "lang:typescript",
        "lang:python",
    ]
    with pytest.raises(ValidationError):
        MissionManifest.model_validate(data)


def test_known_tag_constant_matches_design() -> None:
    """The closed vocabulary mirrors the JSON schema enum (P1-1 design)."""
    # The constants moved together by hand; this guards against silent drift.
    assert "checks_presence_not_expiration" in _KNOWN_TAGS
    assert "goroutine_leak" in _KNOWN_TAGS
    assert "skill:concurrency" in _KNOWN_TAGS
    assert "lang:go" in _KNOWN_TAGS
    assert "lang:elvish" not in _KNOWN_TAGS


def test_sample_yaml_roundtrips_through_validator() -> None:
    """yaml-dumped sample passes the validator; serialisation parity check."""
    data = _load_sample_manifest_dict()
    rebuilt = yaml.safe_load(yaml.safe_dump(deepcopy(data)))
    manifest = MissionManifest.model_validate(rebuilt)
    assert manifest.tags == data["tags"]
