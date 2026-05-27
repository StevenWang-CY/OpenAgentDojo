# Mission 07 — Dependency Misuse (Date Formatting)

| | |
|---|---|
| Repo pack | `data-api-demo` |
| Failure mode | `dependency_misuse` |
| Difficulty | intermediate |
| Diff envelope (p50) | 6 lines |
| Score band, unmodified agent patch | 25–50 |
| Score band, ideal solution | ≥ 90 |

## Design intent

The agent adds `arrow` to `pyproject.toml` and rewrites the formatter
around `arrow.utcnow().to(tz)`. It looks plausible but the offset is
pinned from "now" rather than from the historical `ts`, so the patch
is wrong in both season *and* approach. The right answer is the
stdlib `zoneinfo.ZoneInfo` — two new lines of code, no new
dependency.

Supervisors win by reading `docs/reporting.md`, calling out "use
stdlib zoneinfo", and adding a DST-boundary regression test. The
`no_new_dependencies` validator and the `adds_arrow_dependency`
forbidden-change catch the agent's patch immediately.

## Files

- `mission.yaml`, `agent_patch.diff`, `forbidden_changes.yaml`
- `hidden_tests/test_format_hidden.py`, `hidden_tests/runner.sh`
- `ideal_solution.md`, `acceptance.yaml`
- `prompts/{response,reasoning}.md`, `prompts/intents.yaml`
