# Mission-authoring scaffolder

Interactive CLI that stamps the boilerplate for a new mission. Run from
the repo root:

```bash
python scripts/mission-template/init.py
```

The script prompts for the closed-vocabulary metadata that
`apps/api/app/missions/manifest.py::MissionManifest` requires:

| Prompt                | Closed vocabulary                                                                                      |
| --------------------- | ------------------------------------------------------------------------------------------------------ |
| mission id            | kebab-case, `^[a-z][a-z0-9-]*$`                                                                        |
| repo pack             | `fullstack-auth-demo` \| `data-api-demo` \| `go-orders-service`                                        |
| failure mode          | from `_FAILURE_MODE_TAGS` (`checks_presence_not_expiration`, `goroutine_leak`, `context_dropped`, ...) |
| difficulty            | `beginner` \| `intermediate` \| `advanced`                                                             |
| category              | one of the existing categories (`debugging`, `auth`, `security`, ...) or any free-form string         |
| estimated_minutes     | integer in `5..120`                                                                                    |

It then writes `missions/<NN>-<id>/` populated from the
`scripts/mission-template/template/` skeletons. `NN` is the next free
two-digit prefix; the script refuses to overwrite an existing directory.

## Author workflow

1. Run `python scripts/mission-template/init.py` and answer the prompts.
2. Open the scaffolded `missions/<NN>-<id>/README.md` and walk the
   author checklist — fill out every `TODO:` in the manifest, write the
   canned `agent_patch.diff`, add hidden tests and a runner.
3. Author `missions/_calibration/<id>.yaml` so the recommendation
   engine and `expected_weak_dim` backfill have a baseline.
4. Run `cd apps/api && uv run pytest tests/missions/ -x` to confirm the
   nightly self-tests still pass.
5. Update `apps/api/alembic/versions/0003_seed_missions.py` (or the
   most recent mission seed migration) if the mission ships in the next
   release.

## Why a separate CLI

The manifest schema has enough closed vocabularies — failure-mode tags,
language runtimes, scoring weights — that hand-stamping a new mission
tends to drift from `apps/api/app/missions/manifest.py`. The CLI keeps
the moving parts in lockstep and surfaces the closed vocabulary as a
prompt, so contributors don't have to spelunk through the manifest
loader to figure out which strings are legal.

The script is **not** bundled into the production image. It's a
contributor accelerator that lives at the repo root and runs against a
checkout.

## Adding a new template file

1. Drop the file under `scripts/mission-template/template/`.
2. Use `$variable` (or `${variable}`) for substitution — `safe_substitute`
   leaves unknown identifiers alone, so the templates are forgiving.
   Use `$$` to emit a literal `$` (notably useful inside shell scripts).
3. Add the relative path to `_TEMPLATE_FILES` in
   `scripts/mission-template/init.py` so the scaffolder copies it.
