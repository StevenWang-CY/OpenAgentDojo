"""Forbidden-changes validator tests."""

from __future__ import annotations

from pathlib import Path

import yaml

from app.grading.diff import ParsedDiff
from app.grading.validators import dispatch
from app.grading.validators.forbidden import validate_forbidden_changes


def _write_rules(tmp_path: Path, rules: list[dict]) -> Path:
    out = tmp_path / "forbidden_changes.yaml"
    out.write_text(yaml.safe_dump({"forbidden": rules}), encoding="utf-8")
    return out


def test_regex_present_in_diff_triggers(tmp_path: Path) -> None:
    rules_path = _write_rules(
        tmp_path,
        [
            {
                "id": "hardcoded-user",
                "description": "no hardcoded test user",
                "detection": {
                    "kind": "regex_present_in_diff",
                    "pattern": r'userId\s*=\s*"test',
                    "penalty": 10,
                },
            }
        ],
    )

    diff_text = (
        "--- a/src/a.ts\n+++ b/src/a.ts\n@@ -1,1 +1,2 @@\n"
        ' const x = 1;\n+const userId = "test-user";\n'
    )
    parsed = ParsedDiff(diff_text)

    def fs_reader(_path: str) -> str | None:
        return None

    result = validate_forbidden_changes(parsed, fs_reader, rules_path)
    assert result.passed is False
    assert result.penalty == 10
    assert any("hardcoded-user" in v for v in result.violations)


def test_regex_absent_passes_when_pattern_still_present(tmp_path: Path) -> None:
    rules_path = _write_rules(
        tmp_path,
        [
            {
                "id": "needs-requireauth",
                "description": "requireAuth function must still exist",
                "detection": {
                    "kind": "regex_absent",
                    "file": "backend/middleware/requireAuth.ts",
                    "pattern": r"export\s+function\s+requireAuth",
                    "penalty": 10,
                },
            }
        ],
    )

    parsed = ParsedDiff("")

    def fs_reader(path: str) -> str | None:
        return "export function requireAuth(req, res, next) { next(); }"

    result = validate_forbidden_changes(parsed, fs_reader, rules_path)
    assert result.passed is True
    assert result.penalty == 0


def test_regex_absent_trips_when_pattern_removed(tmp_path: Path) -> None:
    rules_path = _write_rules(
        tmp_path,
        [
            {
                "id": "needs-requireauth",
                "description": "requireAuth function must still exist",
                "detection": {
                    "kind": "regex_absent",
                    "file": "backend/middleware/requireAuth.ts",
                    "pattern": r"export\s+function\s+requireAuth",
                    "penalty": 10,
                },
            }
        ],
    )

    parsed = ParsedDiff("")

    def fs_reader(_path: str) -> str | None:
        return "// nothing here"

    result = validate_forbidden_changes(parsed, fs_reader, rules_path)
    assert result.passed is False
    assert result.penalty == 10


def test_dispatch_routes_to_forbidden_changes(tmp_path: Path) -> None:
    rules_path = _write_rules(tmp_path, [])
    rule = {"kind": "forbidden_changes", "rules_file": "forbidden_changes.yaml"}

    async def fs_reader(_path: str) -> str | None:
        return None

    result = dispatch(
        rule,
        {
            "diff": ParsedDiff(""),
            "fs_reader": fs_reader,
            "manifest_folder": rules_path.parent,
        },
    )
    assert result.kind == "forbidden_changes"
    assert result.passed is True
