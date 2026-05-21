# Mission NN: <Title>

- Mission id: `<kebab-case-id>` (folder: `missions/NN-<id>/`)
- Difficulty: beginner | intermediate | advanced
- Category: auth | testing | security | frontend | api | database | refactoring | agent-safety | review | debugging
- Repo pack: `<fullstack-auth-demo | data-api-demo>`
- Estimated minutes: <N>
- Skills tested: <comma-separated>
- Status: proposed | building | shipped

## Why this scenario

<1-2 paragraphs. What real-world supervisory mistake does this dramatize? Cite a concrete example: a CVE, a public post-mortem, a class of bugs you've personally seen in production.>

## Why this failure mode

<What specifically will the agent get wrong? Why is that a plausible mistake for a real coding agent to make? The failure should be subtle — surface-passing tests, plausible-looking diff, defensible-sounding narration.>

## What we expect users to learn

- Skill A — concrete habit (e.g. "always re-run the affected test suite after applying the agent's patch").
- Skill B — ...
- Skill C — ...

Cap the list at 3-5 items. More than that is a sign the mission is doing too much.

## Common mistakes

- Mistake A — what we predict users will do under-supervised. (e.g. "Accept the patch without opening the diff because the agent's prose was convincing.")
- Mistake B — ...
- Mistake C — ...

These predictions inform the rubric tuning: each mistake should be detectable by an event/validator the rubric weighs.

## Expected score envelopes

| Submission | Expected score |
|---|---|
| Unmodified agent patch | <min>–<max> |
| Ideal solution | ≥ <min_ideal> |
| Empty submission | ≤ <max_empty> |

These numbers go into `missions/NN-<id>/acceptance.yaml` and become the mission self-test.

## Manifest sketch

```yaml
id: <id>
title: "<Title>"
difficulty: ...
category: ...
failure_mode:
  id: <snake_case>
  title: "<short title>"
expected_context:
  required: [<at least 2 paths>]
  recommended: [<paths>]
  discouraged: [<at least 1 path>]
reward_signals:
  prompt_quality:
    must_include_any: [<≥3 keywords>]
expected_diff_lines_p50: <N>
```

(This is a sketch — the authoritative manifest lives in `missions/NN-<id>/mission.yaml` and is schema-validated.)

## Open questions

- <Anything unresolved. e.g. "Should the agent's patch also break a different test we don't currently check?">

## References

- <Public post-mortem, CVE, blog post, or internal incident that inspired the scenario.>
- [IMPLEMENTATION_PLAN.md §29.1](../../IMPLEMENTATION_PLAN.md) (new-mission checklist)
