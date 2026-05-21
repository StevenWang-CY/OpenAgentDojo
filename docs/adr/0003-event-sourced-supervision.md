# ADR 0003: Event-Sourced Supervision

- Status: Accepted
- Date: 2026-05-21
- Deciders: AgentSupervisor Arena team

## Context

The platform has two consumers of "what happened during the session":

1. **The live workspace UI** needs to render a supervision timeline as the user works, and a `ScorePreview` panel that updates as new process signals arrive.
2. **The post-hoc grader** needs the same information, after the session is frozen, to compute the rubric — and to do so identically on every replay.

If we store the timeline as derived UI state and the grader pulls from per-table queries (joining `agent_turns`, `command_runs`, `file_changes`, …), the two views drift. The grader gets one view of "did the user open the diff?", the UI a different view of "what time did the user select context?", and replay becomes a join nightmare.

## Decision

A single append-only table, `supervision_events`, is the authoritative log of everything that happened during a session. Both the live timeline and the grader read from this table only.

```
supervision_events(id BIGSERIAL, session_id UUID, event_type TEXT, payload JSONB, occurred_at TIMESTAMPTZ)
```

Event types are an enum (`session.started`, `context.selected`, `prompt.submitted`, `agent.responded`, `patch.applied`, `diff.opened`, `file.edited`, `command.run`, `submission.requested`, `submission.graded`, etc. — see [IMPLEMENTATION_PLAN.md §6.2](../../IMPLEMENTATION_PLAN.md)). Payload shapes per type are documented in [docs/schemas/event.schema.json](../schemas/event.schema.json).

Domain tables (`agent_turns`, `command_runs`, `file_changes`) remain for relational queries (e.g. "list all turns in this session"), but the **grader treats them as denormalized projections** of the event log, not as inputs.

## Consequences

### Positive

- One source of truth — the live timeline and the score report agree by construction.
- Replays are trivial: feed the event sequence to the grader, get the same score.
- New event types are additive — the schema grows by `oneOf` branch in `event.schema.json`, not by table.
- Time-travel debugging: `SELECT * FROM supervision_events WHERE session_id = ? ORDER BY occurred_at` gives a full session story.

### Negative

- Two-write pattern: emitting an event AND updating the relational row costs an extra `INSERT`. We mitigate with batched inserts inside the same transaction.
- JSONB payloads need a JSON Schema discipline or they drift; see [docs/schemas/event.schema.json](../schemas/event.schema.json).
- Query patterns "give me the last command run" cost a JSON unpack; we index `(session_id, occurred_at)` and project hot fields into the side tables when needed.

### Neutral

- The event log is the natural input for analytics later (funnel: how many users open the diff before submitting?).

## Alternatives considered

- **Derive the timeline from joining domain tables.** Rejected — guaranteed to drift from the grader's view, and ordering across tables becomes painful.
- **Use a real event store (EventStoreDB, Kafka).** Overkill at MVP scale; Postgres JSONB + a single table gets us 95% of the value at 5% of the operational cost.
- **Store the event log only, no side tables.** Tried in a prototype; relational queries for "agent_turns by user" became JSONB-extract-heavy and lost type safety. The hybrid (events + projections) wins.

## References

- [IMPLEMENTATION_PLAN.md §6.2](../../IMPLEMENTATION_PLAN.md)
- [IMPLEMENTATION_PLAN.md §11](../../IMPLEMENTATION_PLAN.md)
- [docs/schemas/event.schema.json](../schemas/event.schema.json)
