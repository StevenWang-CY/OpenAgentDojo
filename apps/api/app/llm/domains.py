"""Closed vocabulary of LLM cache domains + the global prompt version.

Every LLM use site selects exactly one of the literals below. New
domains require an ADR — adding one shifts the cache key space and
every existing cache row in the new domain is empty by definition.

PROMPT_VERSION
--------------
Bump :data:`PROMPT_VERSION` whenever a prompt template under
``app/llm/prompts/`` changes meaningfully (wording, response shape,
context window expectations). The cache key includes
``prompt_version``, so the bump invalidates every row across all
domains and the next call regenerates with the new template. Old rows
remain for audit. This mirrors the ``RUBRIC_VERSION`` discipline at
``app.grading.prompt_judge``.

Per-domain canonicalisation rules
---------------------------------
Each caller is responsible for canonicalising its inputs *before*
hashing — the :func:`app.llm.hashing.canonical_content_hash` helper
is a pure JSON transform and does no rounding. Documented rules:

* ``recommendation_diagnosis`` (P1-2):
    - ``weakest_dim_avg``: ``round(x, 1)`` — a 0.05 floating-point
      drift must not bust the cache.
    - ``recommended_mission_ids``: tuple, preserving order from the
      ranking algorithm. The order IS part of the input — re-ordering
      changes the prose.
    - ``rubric_version``: literal ``"v1"`` (string), so a rubric
      rebalance invalidates the prose alongside the underlying
      scoring.

* ``recommendation_why`` (P1-2):
    - ``mission_id``: bare string.
    - ``weakest_dim``: bare string.
    - ``alignment``: ``round(x, 2)`` — alignment is a 0..1 score; two
      decimal places preserve meaningful deltas without amplifying
      floating-point noise.

* ``critical_moment_polish`` (P0-2 augmentation):
    - ``event_kind``: bare string.
    - ``file_path``: bare string.
    - ``line_range``: tuple ``(start, end)``; both ints.
    - NO raw prompt body or agent response text is included in the
      hash — privacy posture mandates structured description only
      (see P1_DESIGN §0.4.6).

* ``scratchpad_coaching`` (P1-4):
    The actual input dict assembled by
    :mod:`app.reports.coaching` carries the SIX fields below — all
    hashes or bounded scalars so the cache key is content-addressed but
    never reconstructable back to the user's private text (P1_DESIGN
    §0.4.6). Two users with coincidentally identical scratchpads share
    one row precisely because ``user_id`` is NOT part of the key.
      - ``notes_sha256``: SHA-256 hex of the verbatim scratchpad
        bytes. The bytes themselves are NOT in the cache key; they ARE
        sent to Bedrock for generation.
      - ``events_sha256``: SHA-256 hex of the canonical-JSON form of
        the trimmed events timeline (id + offset_seconds + kind +
        truncated summary). Raw payloads are NEVER included.
      - ``mission_id``: bare string.
      - ``mission_version``: int from the loaded manifest — a manifest
        bump invalidates every coaching row for that mission.
      - ``failure_mode``: bare string (the manifest's failure_mode
        title / id), empty string for tutorials.
      - ``score_dimensions_sha256``: SHA-256 hex of the canonical-JSON
        form of ``{dim_name: score}`` for the submission's scored
        dimensions.
      - ``rubric_version``: literal ``"v1"`` — mirrors the
        :data:`app.grading.prompt_judge.RUBRIC_VERSION` discipline so a
        rubric rebalance invalidates coaching prose alongside scoring.

* ``mission_authoring_draft`` (P1-1 contributor tool):
    - ``repo_pack_id``: bare string.
    - ``failure_mode_title``: bare string.
    - ``seed_outline``: bare string (the contributor's bullet-list).
    - Called rarely by a human author; cache hit rate is incidental.
"""

from __future__ import annotations

import os
from typing import Literal

# Bump on prompt-template edits. Invalidates every llm_cache row across
# every domain — same discipline as ``RUBRIC_VERSION``.
#
# Env-rollable: the value is sourced from ``PROMPT_VERSION`` so an
# operator can bump it without redeploying. Importing
# :func:`app.config.get_settings` here would create a cycle
# (``config`` → ``observability`` → ``llm`` → ``domains``) at boot, so
# we read the env directly. The Settings field ``prompt_version`` is
# the canonical declaration and validation surface; this constant
# mirrors it. Both sides default to ``1`` when unset.
try:
    PROMPT_VERSION: int = int(os.environ.get("PROMPT_VERSION", "1"))
    if PROMPT_VERSION < 1:
        raise ValueError("PROMPT_VERSION must be >=1")
except (TypeError, ValueError):
    # A malformed env value should NOT prevent boot — fall back to the
    # safe default and let the Settings validator surface the misconfig
    # at request time.
    PROMPT_VERSION = 1


def get_prompt_version() -> int:
    """Return the active LLM prompt version.

    Function form for callers that want to pick up an env / Settings
    change without re-importing the module. Reads
    :data:`PROMPT_VERSION` so the two stay in lockstep.
    """
    return PROMPT_VERSION


# Closed vocabulary; one Literal per LLM use site below.
LLMDomain = Literal[
    "recommendation_diagnosis",
    "recommendation_why",
    "critical_moment_polish",
    "scratchpad_coaching",
    "mission_authoring_draft",
]

# Frozen set for runtime validation — keep in lockstep with the Literal.
ALLOWED_DOMAINS: frozenset[str] = frozenset(
    (
        "recommendation_diagnosis",
        "recommendation_why",
        "critical_moment_polish",
        "scratchpad_coaching",
        "mission_authoring_draft",
    )
)


def is_known_domain(value: str) -> bool:
    """Return True when ``value`` is one of the closed-vocabulary domains."""
    return value in ALLOWED_DOMAINS
