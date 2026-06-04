"""Forbidden-changes validator.

Reads a ``forbidden_changes.yaml`` rules file and checks the diff + live
filesystem for forbidden patterns.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import yaml

from app.grading.diff import ParsedDiff
from app.grading.validators.base import ValidatorResult

_KIND = "forbidden_changes"


def _load_rules(rules_file_path: Path) -> list[dict[str, Any]]:
    data = yaml.safe_load(rules_file_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(
            f"forbidden_changes rules file {rules_file_path} did not parse to a mapping"
        )
    return cast(list[dict[str, Any]], data.get("forbidden", []))


def validate_forbidden_changes(
    diff: ParsedDiff,
    fs_reader: Callable[[str], str | None],
    rules_file_path: Path,
) -> ValidatorResult:
    """Validate that forbidden changes have not been introduced.

    Rule kinds:
    - ``regex_absent``: the pattern must still be present in ``file`` after the
      patch. If reading the live file shows the pattern is gone, it was
      forbidden-removed.
    - ``regex_present_in_diff``: the pattern must NOT appear in the diff text.
      If it does, that line is a forbidden addition.

    Each rule carries a ``penalty`` (under ``detection``). We sum all triggered
    penalties. ``passed`` is ``True`` iff total penalty is 0.
    """
    rules = _load_rules(rules_file_path)

    violations: list[str] = []
    evidence: list[dict[str, Any]] = []
    total_penalty = 0

    for rule in rules:
        rule_id = rule.get("id", "<unknown>")
        detection = rule.get("detection", {})
        kind = detection.get("kind", "")
        pattern_str = detection.get("pattern", "")
        penalty = int(detection.get("penalty", 0))

        if kind == "regex_absent":
            # The pattern must still be present in the live file.
            # If the pattern is absent → forbidden removal was applied.
            file_path = detection.get("file", "")
            try:
                content = fs_reader(file_path)
            except Exception as exc:
                # If we can't read the file at all, treat as a violation (it may
                # have been deleted entirely).
                content = None
                evidence.append(
                    {
                        "rule_id": rule_id,
                        "kind": kind,
                        "file": file_path,
                        "error": str(exc),
                    }
                )

            if content is None or not re.search(pattern_str, content, re.MULTILINE):
                msg = (
                    f"rule '{rule_id}': required pattern /{pattern_str}/ is absent from {file_path}"
                )
                violations.append(msg)
                evidence.append(
                    {
                        "rule_id": rule_id,
                        "kind": kind,
                        "file": file_path,
                        "pattern": pattern_str,
                        "triggered": True,
                    }
                )
                total_penalty += penalty

        elif kind == "regex_present_in_diff":
            # The pattern must NOT appear among the patch's ADDED lines.
            # Scanning added lines only (not the full diff) is the
            # documented intent — "that line is a forbidden addition". The
            # full-diff text also contains removed (``-``) and context
            # lines, so an *ideal* solution that REMOVES the bad pattern
            # would otherwise trip a "kept_X" rule on its own deletion.
            added_text = "\n".join(diff.all_added_lines())
            if re.search(pattern_str, added_text, re.MULTILINE):
                # Collect matching lines for evidence.
                matching_lines = [
                    line for line in added_text.splitlines() if re.search(pattern_str, line)
                ]
                msg = f"rule '{rule_id}': forbidden pattern /{pattern_str}/ found in diff"
                violations.append(msg)
                evidence.append(
                    {
                        "rule_id": rule_id,
                        "kind": kind,
                        "pattern": pattern_str,
                        "matching_lines": matching_lines[:10],  # cap for JSONB size
                        "triggered": True,
                    }
                )
                total_penalty += penalty

    return ValidatorResult(
        kind=_KIND,
        passed=total_penalty == 0,
        violations=violations,
        penalty=total_penalty,
        evidence=evidence,
    )
