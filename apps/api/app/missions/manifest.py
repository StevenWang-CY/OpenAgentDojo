"""Strict Pydantic v2 model of the ``mission.yaml`` schema (plan §7.1).

The manifest is the contract between content authors and the runtime. Every
field is typed; the scoring weights must equal the canonical
(30/15/15/10/10/10/10) distribution sourced from
:data:`app.grading.dimensions.RUBRIC_DIMENSIONS`. (The original plan numbers
were 30/20/15/10/10/10/5 — the grader uses the rebalanced distribution.)
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

from app.grading.dimensions import DIMENSION_MAX

Difficulty = Literal["beginner", "intermediate", "advanced"]
# P1-1 — ``go122`` is the third sandbox runtime (Go 1.22 / chi) shipped with
# the ``go-orders-service`` repo pack. The base image is built off
# ``golang:1.22-bookworm`` (see ``missions/_shared/docker/go-orders.Dockerfile``)
# and the grader bridges Go's ``-json`` test events into the same
# ``{name,status,duration_ms,file}`` envelope the TS/Py runners produce via
# ``missions/_shared/docker/runners/go-runner.sh``.
LanguageRuntime = Literal["node20", "python312", "go122"]
MissionKind = Literal["standard", "tutorial"]

# P1-2 — closed vocabulary for ``MissionManifest.expected_weak_dim``.
# Each standard mission declares the single dimension it is primarily
# designed to exercise; the recommendation engine reads this to align
# the user's weakest dimension against the mission catalogue.
ExpectedWeakDim = Literal[
    "final_correctness",
    "verification",
    "agent_review",
    "prompt_quality",
    "context_selection",
    "safety",
    "diff_minimality",
]


# P1-1 — closed vocabulary for ``MissionManifest.tags``. Three families:
#
# * Failure-mode tags — exactly one of these must appear on every standard
#   mission, and it MUST match the manifest's ``failure_mode.id`` so the
#   catalog filter never drifts from the report copy.
# * Skill tags (``skill:*``) — optional; let the catalog filter on
#   underlying skills (concurrency, typing, auth, etc.).
# * Language tags (``lang:*``) — optional; auto-inferable from the repo
#   pack but allowed on the manifest for legibility.
#
# New tags require an ADR. The vocabulary mirrors
# ``docs/schemas/mission.schema.json``; keep both in sync.
_FAILURE_MODE_TAGS: frozenset[str] = frozenset(
    {
        "checks_presence_not_expiration",
        "overfitted_visible_test",
        "wrong_layer_committed",
        "missing_regression_test",
        "race_condition",
        "context_dropped",
        "error_wrapped_swallowed",
        "dependency_misuse",
        "security_check_removed",
        "typecheck_ignored",
        "api_contract_drift",
        "excessive_rewrite",
        "goroutine_leak",
        # P1-7 — second mission wave (missions 14-20).
        "optimistic_update_no_rollback",
        "zod_any_escape",
        "pandas_iterrows_perf_trap",
        "pydantic_silent_coercion",
        "channel_deadlock_on_cancel",
        "asyncio_shield_misuse",
        "sql_tx_leak_early_return",
    }
)
_SKILL_TAGS: frozenset[str] = frozenset(
    {
        "skill:concurrency",
        "skill:typing",
        "skill:auth",
        "skill:http",
        "skill:sql",
        "skill:cli",
        # P1-7 — second mission wave.
        "skill:ui",
        "skill:data",
    }
)
_LANGUAGE_TAGS: frozenset[str] = frozenset({"lang:typescript", "lang:python", "lang:go"})
_KNOWN_TAGS: frozenset[str] = _FAILURE_MODE_TAGS | _SKILL_TAGS | _LANGUAGE_TAGS


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
    # RESERVED / NOT-YET-CONSUMED: the registered ``no_new_dependencies``
    # validator calls ``validate_no_new_deps(diff)`` with no allow-list, so
    # ``allowed`` is parsed and round-tripped but has ZERO effect on grading
    # today. It is retained (rather than dropped) because curated missions
    # already declare it; do not assume editing it changes any score until a
    # validator change wires it in. See the module note on reserved fields.
    allowed: list[str] = Field(default_factory=list)


class ValidatorNoSecretsExposed(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["no_secrets_exposed"]


class ValidatorNoValidationRemoved(BaseModel):
    # Matches the registered ``no_validation_removed`` validator
    # (app/grading/validators/no_validation_removed.py), which flags removal
    # of auth/validation guard clauses from the diff. ``patterns`` is optional
    # and is *appended* to the validator's built-in guard-clause defaults — a
    # mission only needs to declare it to widen the default set, never to
    # bootstrap it. Without this union variant the registered validator was
    # unreachable from any manifest (P2 contract-integrity fix).
    model_config = ConfigDict(extra="forbid")
    kind: Literal["no_validation_removed"]
    patterns: list[str] = Field(default_factory=list)


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
    | Annotated[ValidatorNoSecretsExposed, Tag("no_secrets_exposed")]
    | Annotated[ValidatorNoValidationRemoved, Tag("no_validation_removed")],
    Discriminator(_validator_discriminator),
]


# --- scoring + signals ---

# Canonical scoring distribution — sourced from the grader's single rubric
# constant (apps/api/app/grading/dimensions.py:RUBRIC_DIMENSIONS) so a manifest
# that validates here is guaranteed to match what the grader actually applies.
# Previously this duplicated the §11.1 plan numbers (30/20/15/10/10/10/5) and
# silently drifted from the runtime rubric (30/15/15/10/10/10/10) — every
# mission YAML asserted weights the grader never honoured.
SCORING_WEIGHTS_CANONICAL: dict[str, int] = dict(DIMENSION_MAX)


class ScoringWeights(BaseModel):
    """Scoring weights with hard constants — must match RUBRIC_DIMENSIONS."""

    model_config = ConfigDict(extra="forbid")

    final_correctness: int = 0
    verification: int = 0
    agent_review: int = 0
    prompt_quality: int = 0
    context_selection: int = 0
    safety: int = 0
    diff_minimality: int = 0

    @model_validator(mode="after")
    def _enforce_canonical(self) -> ScoringWeights:
        # Tutorial missions ship with all-zero weights — the runner
        # short-circuits before they ever run through the scorer, so the
        # canonical-weights invariant only applies to ``kind == "standard"``.
        # We can't see ``kind`` from here, so the post-load validator on
        # ``MissionManifest`` enforces this branch; allow the all-zero
        # fast-path here and reject any *non-zero non-canonical* shape so
        # mis-edited standard missions still fail at parse time.
        actuals = {k: getattr(self, k) for k in SCORING_WEIGHTS_CANONICAL}
        if all(v == 0 for v in actuals.values()):
            return self
        for k, v in SCORING_WEIGHTS_CANONICAL.items():
            actual = actuals[k]
            if actual != v:
                raise ValueError(
                    f"scoring_weights.{k} must be {v}, got {actual}"
                    " — see IMPLEMENTATION_PLAN.md §11.1"
                )
        if sum(SCORING_WEIGHTS_CANONICAL.values()) != 100:  # pragma: no cover
            raise ValueError("canonical weights drift — file a bug")
        return self


# RESERVED-FIELD POLICY (P1/P2 contract-honesty note)
# ----------------------------------------------------
# Several ``reward_signals`` sub-fields below are parsed + schema-validated but
# NOT YET CONSUMED by ``app.grading.score`` — editing them has zero effect on
# any computed score. They are documented here (and flagged inline) so the
# drift between "the schema accepts it" and "the grader honours it" is explicit
# rather than a silent config-that-does-nothing trap. They are retained (not
# deleted) because curated mission YAMLs already declare them and the loader is
# ``extra="forbid"``; removing them would break the loader/calibration. Each
# reserved field is annotated below; the CONSUMED fields are left unannotated.
#
# Currently CONSUMED by score.py:
#   * PromptQualitySignals.must_include_any, .bonus_keywords
#   * VerificationSignals.require_targeted_test
#   * SafetySignals.must_not_run_commands
# Currently RESERVED (not consumed): every field tagged ``RESERVED`` below.


class PromptQualitySignals(BaseModel):
    model_config = ConfigDict(extra="forbid")
    must_include_any: list[str] = Field(default_factory=list)
    bonus_keywords: list[str] = Field(default_factory=list)
    # RESERVED / NOT-YET-CONSUMED: the prompt-quality scorer hard-codes its
    # short-prompt penalty thresholds (40/80 chars); this field is parsed but
    # never read. Editing it does not change any score.
    penalty_if_under_chars: int = 40


class VerificationSignals(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # RESERVED / NOT-YET-CONSUMED: the verification scorer credits typecheck +
    # lint by event category directly and never reads this list. Parsed but
    # inert — editing it does not change any score.
    required_categories: list[str] = Field(default_factory=list)
    # RESERVED / NOT-YET-CONSUMED: no "ran before patch" bonus is implemented.
    # Parsed but inert.
    bonus_if_run_before_patch: bool = False
    require_targeted_test: str | None = None


class AgentReviewSignals(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # RESERVED / NOT-YET-CONSUMED: the agent-review scorer always credits a
    # post-patch ``diff.opened`` (with dwell gating); it never gates that
    # credit on this flag. Parsed but inert.
    require_diff_open: bool = True
    # RESERVED / NOT-YET-CONSUMED: the revert/edit-after-diff bonus is awarded
    # unconditionally from the event log, never gated on this flag. Parsed but
    # inert.
    bonus_if_revert_or_edit_after_diff: bool = True


class SafetySignals(BaseModel):
    model_config = ConfigDict(extra="forbid")
    must_not_run_commands: list[str] = Field(default_factory=list)
    # RESERVED / NOT-YET-CONSUMED: the safety scorer credits "no new deps" from
    # the ``no_new_dependencies`` validator result, never from this flag.
    # Parsed but inert.
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
    # P0-1 — ``tutorial`` short-circuits the grading runner: a tutorial
    # mission persists no Submission row, awards no badges, and is
    # excluded from the public catalog grid + skills aggregation. The
    # one and only tutorial mission today is ``orientation``.
    kind: MissionKind = "standard"
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

    # P1-1 — closed-vocabulary tag list (see ``_KNOWN_TAGS``). Tutorials may
    # ship empty tags; standard missions MUST carry at least one failure-mode
    # tag, and that tag MUST equal ``failure_mode.id`` so the catalog filter
    # never drifts from the report copy. Cap at 8 to keep the catalog filter
    # readable; unique entries enforced by the ``set(...)`` length check below.
    tags: list[str] = Field(default_factory=list, max_length=8)

    # P1-2 — the single rubric dimension this mission is *primarily designed
    # to exercise*. Drives the deterministic recommendation engine: a user
    # whose weakest dimension is ``agent_review`` is steered toward missions
    # whose ``expected_weak_dim == "agent_review"``. Required for standard
    # missions, allowed-but-null for tutorials (the orientation surface does
    # not appear in the recommendation ladder). The closed vocabulary mirrors
    # ``apps/api/app/grading/dimensions.py::RUBRIC_DIMENSIONS``; keep both in
    # lockstep with ``docs/schemas/mission.schema.json``.
    expected_weak_dim: ExpectedWeakDim | None = None

    @model_validator(mode="after")
    def _enforce_kind_invariants(self) -> MissionManifest:
        """Tutorial-vs-standard mission invariants.

        * Standard missions must declare canonical, non-zero scoring weights.
          The ``ScoringWeights`` validator already allows the all-zero
          fast-path, so we enforce the non-zero contract one level up where
          we can read ``kind``.
        * Tutorial missions inherit the catalog-suppression default: they are
          never auto-listed in the public mission grid. The orientation
          surface in the FE consumes them directly via the ``kind`` field.
        """
        weights = self.scoring_weights
        weight_sum = sum(
            getattr(weights, k)
            for k in (
                "final_correctness",
                "verification",
                "agent_review",
                "prompt_quality",
                "context_selection",
                "safety",
                "diff_minimality",
            )
        )
        if self.kind == "standard" and weight_sum == 0:
            raise MissionConfigError(
                f"mission '{self.id}' is kind=standard but scoring_weights are "
                "all zero — set the canonical 30/15/15/10/10/10/10 weights "
                "or change kind to 'tutorial'."
            )
        if self.kind == "tutorial" and self.published:
            # Tutorial missions surface via the dedicated "Start here"
            # affordance, not the catalog grid. Letting them be
            # ``published`` would silently leak them into the public list.
            raise MissionConfigError(
                f"tutorial mission '{self.id}' must set published=false; "
                "the FE renders tutorials through the orientation surface."
            )
        # P1-2 — standard missions must declare the dimension they exercise
        # so the recommendation engine can align it against the user's
        # weakest dimension. Tutorials are exempt because the orientation
        # surface never appears in the recommendation ladder.
        if self.kind == "standard" and self.expected_weak_dim is None:
            raise MissionConfigError(
                f"mission '{self.id}' (kind=standard) is missing the "
                "``expected_weak_dim`` field. Set it to the single rubric "
                "dimension this mission primarily exercises (one of "
                "final_correctness, verification, agent_review, "
                "prompt_quality, context_selection, safety, "
                "diff_minimality) — see P1_DESIGN.md §P1-2."
            )
        return self

    @field_validator("expected_files")
    @classmethod
    def _no_dupes(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("expected_files contains duplicates")
        return v

    @field_validator("tags")
    @classmethod
    def _tags_in_closed_vocabulary(cls, v: list[str]) -> list[str]:
        """Reject any tag outside the closed vocabulary (P1-1).

        Surfaces the unknown values directly so authors can see exactly which
        token tripped the gate — a generic "invalid tag" would force them to
        diff their manifest against the schema by hand.
        """
        if len(set(v)) != len(v):
            raise ValueError("tags contains duplicate entries")
        unknown = sorted(set(v) - _KNOWN_TAGS)
        if unknown:
            raise ValueError(
                "tags contains unknown entries "
                f"{unknown!r}; allowed values are listed in "
                "apps/api/app/missions/manifest.py::_KNOWN_TAGS "
                "(failure-mode + skill:* + lang:* families)."
            )
        return v

    @model_validator(mode="after")
    def _failure_mode_tag_matches_manifest(self) -> MissionManifest:
        """Enforce the failure-mode-tag / ``failure_mode.id`` lockstep (P1-1).

        Standard missions MUST carry exactly one failure-mode tag, and it MUST
        equal the ``failure_mode.id`` field — otherwise the catalog filter
        could surface a mission under a different failure-mode label than the
        report copy uses. Tutorials are exempt: the orientation flow does not
        live in the catalog grid and is not scored, so a tag list is optional.
        """
        present_failure_tags = [t for t in self.tags if t in _FAILURE_MODE_TAGS]
        if self.kind == "tutorial":
            return self
        if not present_failure_tags:
            raise MissionConfigError(
                f"mission '{self.id}' (kind=standard) carries no failure-mode "
                "tag — add the canonical tag matching `failure_mode.id` "
                f"({self.failure_mode.id!r}) to `tags:`."
            )
        if len(present_failure_tags) > 1:
            raise MissionConfigError(
                f"mission '{self.id}' carries multiple failure-mode tags "
                f"{present_failure_tags!r}; exactly one is allowed so the "
                "catalog filter stays unambiguous."
            )
        (mode_tag,) = present_failure_tags
        if mode_tag != self.failure_mode.id:
            raise MissionConfigError(
                f"mission '{self.id}' failure-mode tag {mode_tag!r} does not "
                f"match `failure_mode.id` {self.failure_mode.id!r}. Update "
                "the tag or the failure_mode id so they agree (the catalog "
                "filter and the report copy must use the same key)."
            )
        return self

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
