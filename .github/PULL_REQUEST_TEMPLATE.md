<!--
  AgentSupervisor Arena — Pull Request checklist.
  See IMPLEMENTATION_PLAN.md §29.3 for the full review checklist.
-->

## Summary

<!-- 1–3 sentences. What changed, why now. Reference the milestone (M0–M8) if relevant. -->

## Changes

- [ ] Section/area touched:
- [ ] Linked issues / docs:

## PR review checklist

- [ ] Tests added/updated
- [ ] Migrations include up + down
- [ ] No secrets in diff
- [ ] Determinism preserved (no `time.time()`, no `random` without seed on graded code paths)
- [ ] Telemetry events added for new user actions (where applicable)
- [ ] Accessibility for new UI (axe pass)
- [ ] Docs updated (`CONTEXT.md`, ADR, runbook) if a new noun, decision, or operational step landed

## Screenshots / recordings

<!-- For UI changes. Drag images or paste a Loom link. -->

## Risk & rollback

<!-- What happens if this is wrong in prod? How do we roll back? -->
