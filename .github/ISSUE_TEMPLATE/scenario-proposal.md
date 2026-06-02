---
name: Scenario proposal (new mission)
about: Propose a new mission for the catalogue
title: "[scenario-proposal] "
labels: ["scenario-proposal", "mission"]
---

> Open this issue **before** you write the manifest. A wrong failure-mode
> framing is expensive to undo after a mission ships.

## Failure mode the mission exercises

<!-- Which supervisor behaviour does this mission train? E.g. "missed the
expiration check in a session-cookie diff," "trusted the agent's
narration without opening the diff," "ran the wrong test suite." -->

## Target difficulty

<!-- One of: easy / medium / hard. Easy missions are runnable end-to-end
in under 8 minutes by a returning user; hard missions can take ~25. -->

## Repo pack

<!-- Which repo pack does this build on? See missions/_shared/repos/. If
you need a new pack, describe it; pack additions need separate sign-off. -->

## Expected context shape

<!-- Which files should the supervisor select before prompting? List the
"required" set and the "discouraged" set. -->

- required:
- discouraged:

## Agent patch outline

<!-- ~3 lines describing what the deliberately-flawed agent patch does.
The mistake should be plausible (looks right at a glance) and load-
bearing (the supervisor can catch it by reading the diff carefully or by
running the right test). -->

## Hidden test sketch

<!-- What hidden test catches the failure? One or two sentences. -->

## Ideal solution shape

<!-- One sentence describing the canonical fix. -->

## Why this mission

<!-- Why does the catalogue benefit from this one? What does it teach
that existing missions don't? -->
