# Mission: $mission_id

> Scaffolded by `scripts/mission-template/init.py`. Replace every TODO
> in this directory before opening a PR.

## Author checklist

- [ ] Flesh out `mission.yaml`: brief, expected_files, validators, scoring
      weights, reward signals.
- [ ] Write `agent_patch.diff` so that *applying it* reproduces the
      failure mode (`$failure_mode`). Visible tests should still pass.
- [ ] Add at least one regression test that fails on the broken patch
      and passes on the ideal solution. Put it under `hidden_tests/`.
- [ ] Wire `hidden_tests/runner.sh` to run the hidden suite inside the
      sandbox and emit `results.json` in the grader envelope shape.
- [ ] Author `missions/_calibration/$mission_id.yaml` so the recommendation
      engine and `expected_weak_dim` have a baseline.
- [ ] Update `missions/<NN>-$mission_id/ideal_solution.md` (and ideally
      `ideal_solution.diff`).
- [ ] If the mission ships with the next release, update
      `apps/api/alembic/versions/0003_seed_missions.py` (or the most
      recent mission-seed migration).

## Repo pack

This mission targets **`$repo_pack`** (`$language` / `$language_runtime`).
The pack lives at `missions/_shared/repos/$repo_pack/`.

## Difficulty & timing

- Difficulty: `$difficulty`
- Estimated time: `$estimated_minutes` minutes (5-120 supported)

## Failure mode

`$failure_mode` — see the closed vocabulary in
`apps/api/app/missions/manifest.py::_FAILURE_MODE_TAGS` for the full list
and `docs/schemas/mission.schema.json` for the JSON-schema contract.
