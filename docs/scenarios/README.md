# Mission Scenarios

This directory holds the **human-facing design notes** for each mission. Engineers and content authors collaborate here to make sure a mission is pedagogically sound before the manifest lands in `missions/<id>/`.

For each shipped mission, this directory should contain a design note answering five questions:

1. **Why this scenario?** What real-world failure does it teach?
2. **Why this failure mode?** Why does this *agent* mistake represent the failure?
3. **What do we expect users to learn?** Specific skills or habits.
4. **What are the common mistakes?** Predicted ways users will under-supervise.
5. **What's the expected score envelope?** Mirrors `acceptance.yaml`.

The design note is independent of the executable manifest. The manifest is the contract; the design note is the rationale.

## Files

- [template.md](./template.md) — copy this when starting a new scenario.
- [01-auth-cookie-expiration.md](./01-auth-cookie-expiration.md) — Mission 01 design note.

## Workflow

1. **Propose.** Open a design note from the template and circulate to the team.
2. **Discuss.** Resolve concerns about realism, difficulty, overlap with other missions.
3. **Build.** Author `mission.yaml`, agent patch, hidden tests, forbidden changes, ideal solution, prompts, acceptance.
4. **Self-test.** Run `pnpm validate:missions` and the mission self-tests (`pnpm test:missions:01`).
5. **Ship.** PR includes the design note and the manifest. The design note is the reviewer's primer.

## Relationship to other docs

- The manifest schema lives in [docs/schemas/mission.schema.json](../schemas/mission.schema.json).
- The new-mission checklist lives in [IMPLEMENTATION_PLAN.md §29.1](../../IMPLEMENTATION_PLAN.md).
- The grading rubric that scores submissions to these missions lives in [docs/grading.md](../grading.md).
