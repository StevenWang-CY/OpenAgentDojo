"""Shared validator result type."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ValidatorResult:
    kind: str
    passed: bool
    violations: list[str] = field(default_factory=list)
    penalty: int = 0
    evidence: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "passed": self.passed,
            "violations": self.violations,
            "penalty": self.penalty,
            "evidence": self.evidence,
        }
