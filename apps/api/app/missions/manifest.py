"""Strict Pydantic v2 model of the ``mission.yaml`` schema (plan §7.1).

The manifest is the contract between content authors and the runtime. Every
field is typed; the scoring weights must equal the canonical (30/20/15/10/10/10/5)
distribution.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    Tag,
    field_validator,
    model_validator,
)

Difficulty = Literal["beginner", "intermediate", "advanced"]
LanguageRuntime = Literal["node20", "python312"]


class MissionConfigError(ValueError):
    """Raised when a mission manifest fails a hard configuration invariant.

    Subclasses :class:`ValueError` so Pydantic's own validators integrate
    naturally (they accept ValueErrors as the canonical failure type) while
    callers that catch ``MissionConfigError`` see a more specific signal.
    """


class RepoConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pack: str
    initial_commit: str
    workdir: str = "/workspace"
    language_runtime: LanguageRuntime
    setup_commands: list[str] = Field(default_factory=list)
    ready_check: str | None = None
    test_commands: dict[str, str] = Field(default_factory=dict)


class FailureMode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    description: str
    hint_after_submit_if_missed: str | None = None


class ExpectedContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    required: list[str] = Field(default_factory=list, min_length=2)
    recommended: list[str] = Field(default_factory=list)
    discouraged: list[str] = Field(default_factory=list)


class AgentAppliesWhen(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt_min_chars: int = 40
    prompt_must_contain_any: list[str] = Field(default_factory=list)


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patch_file: str
    response_template: str
    reasoning_template: str | None = None
    # Optional path to an intents keyword file (e.g. prompts/intents.yaml)
    # consumed by the deterministic agent intent classifier (see §8).
    intents_file: str | None = None
    applies_when: AgentAppliesWhen = Field(default_factory=AgentAppliesWhen)
    # ``auto`` is advisory today — patches still require the user to call
    # ``POST /sessions/{id}/patches/{turn_id}/apply``. Honouring auto-apply
    # is tracked separately so we don't ship a public field whose semantics
    # diverge from the implementation. Keep the literal set narrow.
    apply_mode: Literal["on_user_confirm", "auto"] = "on_user_confirm"


class HiddenTests(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: str
    expected_pass: list[str] = Field(default_factory=list)


# --- discriminated validators ---


class ValidatorForbiddenChanges(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["forbidden_changes"]
    rules_file: str


class ValidatorDiffScope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["diff_scope"]
    max_files_changed: int | None = None
    max_added_lines: int | None = None
    must_touch_any_of: list[str] = Field(default_factory=list)
    must_not_touch: list[str] = Field(default_factory=list)


class ValidatorRegressionTestRequired(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["regression_test_required"]
    test_globs: list[str]
    keywords_any_of: list[str]


class ValidatorNoSkippedTests(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["no_skipped_tests"]
    patterns: list[str]


class ValidatorNoNewDependencies(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["no_new_dependencies"]
    allowed: list[str] = Field(default_factory=list)


class ValidatorNoSecretsExposed(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["no_secrets_exposed"]


def _validator_discriminator(v: Any) -> str:
    if isinstance(v, dict):
        return str(v.get("kind", ""))
    return str(getattr(v, "kind", ""))


Validator = Annotated[
    Annotated[ValidatorForbiddenChanges, Tag("forbidden_changes")]
    | Annotated[ValidatorDiffScope, Tag("diff_scope")]
    | Annotated[ValidatorRegressionTestRequired, Tag("regression_test_required")]
    | Annotated[ValidatorNoSkippedTests, Tag("no_skipped_tests")]
    | Annotated[ValidatorNoNewDependencies, Tag("no_new_dependencies")]
    | Annotated[ValidatorNoSecretsExposed, Tag("no_secrets_exposed")],
    Discriminator(_validator_discriminator),
]


# --- scoring + signals ---

# Canonical scoring distribution — sourced from the grader's single rubric
# constant (apps/api/app/grading/dimensions.py:RUBRIC_DIMENSIONS) so a manifest
# that validates here is guaranteed to match what the grader actually applies.
# Previously this duplicated the §11.1 plan numbers (30/20/15/10/10/10/5) and
# silently drifted from the runtime rubric (30/15/15/10/10/10/10) — every
# mission YAML asserted weights the grader never honoured.
from app.grading.dimensions import DIMENSION_MAX

SCORING_WEIGHTS_CANONICAL: dict[str, int] = dict(DIMENSION_MAX)


class ScoringWeights(BaseModel):
    """Scoring weights with hard constants — must match RUBRIC_DIMENSIONS."""

    model_config = ConfigDict(extra="forbid")

    final_correctness: int
    verification: int
    agent_review: int
    prompt_quality: int
    context_selection: int
    safety: int
    diff_minimality: int

    @model_validator(mode="after")
    def _enforce_canonical(self) -> ScoringWeights:
        for k, v in SCORING_WEIGHTS_CANONICAL.items():
            actual = getattr(self, k)
            if actual != v:
                raise ValueError(
                    f"scoring_weights.{k} must be {v}, got {actual}"
                    " — see IMPLEMENTATION_PLAN.md §11.1"
                )
        if sum(SCORING_WEIGHTS_CANONICAL.values()) != 100:  # pragma: no cover
            raise ValueError("canonical weights drift — file a bug")
        return self


class PromptQualitySignals(BaseModel):
    model_config = ConfigDict(extra="forbid")
    must_include_any: list[str] = Field(default_factory=list)
    bonus_keywords: list[str] = Field(default_factory=list)
    penalty_if_under_chars: int = 40


class VerificationSignals(BaseModel):
    model_config = ConfigDict(extra="forbid")
    required_categories: list[str] = Field(default_factory=list)
    bonus_if_run_before_patch: bool = False
    require_targeted_test: str | None = None


class AgentReviewSignals(BaseModel):
    model_config = ConfigDict(extra="forbid")
    require_diff_open: bool = True
    bonus_if_revert_or_edit_after_diff: bool = True


class SafetySignals(BaseModel):
    model_config = ConfigDict(extra="forbid")
    must_not_run_commands: list[str] = Field(default_factory=list)
    must_not_introduce_deps: bool = True


class RewardSignals(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt_quality: PromptQualitySignals = Field(default_factory=PromptQualitySignals)
    verification: VerificationSignals = Field(default_factory=VerificationSignals)
    agent_review: AgentReviewSignals = Field(default_factory=AgentReviewSignals)
    safety: SafetySignals = Field(default_factory=SafetySignals)


# --- root ---


class MissionManifest(BaseModel):
    """Top-level mission.yaml schema."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9-]+$")
    version: int = 1
    title: str
    short_description: str = ""
    difficulty: Difficulty
    category: str
    estimated_minutes: int = Field(gt=0)
    skills_tested: list[str] = Field(default_factory=list)

    repo: RepoConfig
    brief: str = ""
    failure_mode: FailureMode

    expected_files: list[str] = Field(default_factory=list)
    expected_context: ExpectedContext

    agent: AgentConfig
    visible_tests: list[str] = Field(default_factory=list)
    hidden_tests: HiddenTests

    validators: list[Validator] = Field(default_factory=list)
    scoring_weights: ScoringWeights
    reward_signals: RewardSignals = Field(default_factory=RewardSignals)

    expected_diff_lines_p50: int = Field(default=20, gt=0)
    # Default False so a half-written mission can't accidentally ship to the
    # public catalog. Each curated mission must opt in explicitly (P2-B2).
    published: bool = False

    @field_validator("expected_files")
    @classmethod
    def _no_dupes(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("expected_files contains duplicates")
        return v

    @model_validator(mode="after")
    def _require_test_commands_for_visible_suites(self) -> MissionManifest:
        """Reject manifests that declare visible tests but ship no commands.

        Mission grading routes ``repo.test_commands`` through both drivers
        (docker + local) and the score engine credits ``visible_tests_pass``
        based on the resulting suites. A manifest that lists visible test
        labels in ``visible_tests`` but never wires up ``repo.test_commands``
        used to score a free ``+8`` for visible tests passing because the
        driver returned an empty result set and the grader treated empty as
        pass. Surface this drift at load time instead (P0-B4).
        """
        if self.visible_tests and not self.repo.test_commands:
            raise MissionConfigError(
                f"mission '{self.id}' declares visible_tests "
                f"{self.visible_tests!r} but repo.test_commands is empty — "
                "add at least one command so the grader can verify them."
            )
        return self
