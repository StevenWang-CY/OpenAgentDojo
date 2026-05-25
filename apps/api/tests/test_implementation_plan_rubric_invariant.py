"""Anti-drift check: IMPLEMENTATION_PLAN.md §11.1 must agree with dimensions.py.

The rubric lives in ``apps/api/app/grading/dimensions.py`` as a single
``RUBRIC_DIMENSIONS`` tuple — that is the runtime source of truth. The
plan's §11.1 table is the human-readable mirror. Whenever the rubric is
re-balanced (see ADR 0011), both surfaces must move together; this test
fails the build when they drift apart.

The test reads ``IMPLEMENTATION_PLAN.md`` from the repo root, walks the
§11.1 Weight table, and asserts every row's max matches the constant
shipped in ``dimensions.py``. It is intentionally strict about row
matching (we filter on known dimension labels) so stray markdown like
header rows or formatting examples elsewhere in the file can't trip
the parser.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.grading.dimensions import RUBRIC_DIMENSIONS

# The plan lives at the repo root. ``Path(__file__).resolve().parents``:
#   [0] this file
#   [1] tests/
#   [2] apps/api/
#   [3] apps/
#   [4] repo root
_PLAN_PATH = Path(__file__).resolve().parents[3] / "IMPLEMENTATION_PLAN.md"

# Heading that anchors the §11.1 table — kept tight so we don't pick up
# unrelated sections that happen to mention "Weight" elsewhere.
_TABLE_HEAD = "### 11.1 Weight table"

# Match a table row like:
#   | Verification Discipline | 15 | Command run events |
# Capture (label, max). Only rows where ``label`` matches a known dimension
# are honoured — the regex is permissive about whitespace and trailing
# garbage so a future column add doesn't break the parser.
_ROW_RE = re.compile(r"\|\s*([A-Za-z][A-Za-z ]+?)\s*\|\s*(\*\*)?(\d+)\1?\s*\|")

# Plan label → dimensions.py key. Keep this aligned with §11.1 prose;
# adding a dimension means updating BOTH this dict and the table.
_DIMENSION_LABELS: dict[str, str] = {
    "Final Patch Correctness": "final_correctness",
    "Verification Discipline": "verification",
    "Agent Output Review": "agent_review",
    "Prompt Quality": "prompt_quality",
    "Context Selection": "context_selection",
    "Safety Awareness": "safety",
    "Diff Minimality": "diff_minimality",
}


def _extract_table_section(text: str) -> str:
    """Return the chunk of the plan that holds the §11.1 table.

    Slices from the section heading to the next ``###`` so the regex
    never reaches into §11.2 or beyond. Raises if the heading is
    missing — that itself is a drift signal worth failing on.
    """
    if _TABLE_HEAD not in text:
        raise AssertionError(
            f"IMPLEMENTATION_PLAN.md is missing the {_TABLE_HEAD!r} heading; "
            "the rubric invariant test has nothing to read. Either the "
            "section was renamed (update _TABLE_HEAD) or the table was "
            "deleted (restore §11.1 — the FE renders that prose)."
        )
    after_head = text.split(_TABLE_HEAD, 1)[1]
    # Stop at the next H3 or H2 so we don't accidentally scoop a stray
    # number out of §11.2.x.
    return re.split(r"\n#{2,3}\s", after_head, maxsplit=1)[0]


def test_plan_section_11_1_matches_dimensions_table() -> None:
    text = _PLAN_PATH.read_text(encoding="utf-8")
    section = _extract_table_section(text)

    parsed: dict[str, int] = {}
    for raw_label, _bold, max_str in _ROW_RE.findall(section):
        label = raw_label.strip()
        if label not in _DIMENSION_LABELS:
            continue
        # Reject duplicates — two rows for the same dimension is itself
        # drift worth surfacing.
        key = _DIMENSION_LABELS[label]
        assert key not in parsed, (
            f"IMPLEMENTATION_PLAN.md §11.1 has multiple rows for {label!r} — "
            "the table should list each dimension exactly once."
        )
        parsed[key] = int(max_str)

    shipped = dict(RUBRIC_DIMENSIONS)
    assert parsed == shipped, (
        "IMPLEMENTATION_PLAN.md §11.1 disagrees with "
        "apps/api/app/grading/dimensions.py:\n"
        f"  plan §11.1: {parsed}\n"
        f"  shipped:    {shipped}\n"
        "Update the plan to match the shipped weights. See ADR 0011 for "
        "the rationale behind the current weights."
    )


def test_plan_section_11_1_total_is_100() -> None:
    """Belt-and-braces: the sum of the §11.1 numbers must be 100.

    Defends against a renumbering that updates each row consistently with
    dimensions.py but accidentally produces a 95- or 105-point rubric in
    the table prose.
    """
    text = _PLAN_PATH.read_text(encoding="utf-8")
    section = _extract_table_section(text)

    dim_total = 0
    for raw_label, _bold, max_str in _ROW_RE.findall(section):
        label = raw_label.strip()
        if label not in _DIMENSION_LABELS:
            continue
        dim_total += int(max_str)
    assert dim_total == 100, (
        f"IMPLEMENTATION_PLAN.md §11.1 dimensions sum to {dim_total}, not 100. "
        "The 100-point rubric is a load-bearing product invariant (see ADR 0006)."
    )
