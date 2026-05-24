/**
 * FE↔BE event-type enum coverage.
 *
 * The FastAPI ``EventEmitter.emit`` accepts ``event_type: str`` so any new
 * literal added on the BE side risks silently slipping past the type system
 * here. The mirror set ``KNOWN_BE_EVENT_TYPES`` is hand-maintained: when an
 * engineer adds a new ``event_type="..."`` literal to the BE, they MUST
 * also extend ``packages/shared-types/src/events.ts`` AND append the same
 * string here, otherwise this test fails.
 *
 * The Python-side counterpart (``apps/api/tests/test_event_contract.py``)
 * keeps the BE honest in the opposite direction by scanning every
 * ``event_type=`` call site.
 */
import { describe, expect, it } from "vitest";
import { SupervisionEventType } from "@arena/shared-types";

/**
 * Locked list of every event type the BE is allowed to emit on the wire.
 * Sorted alphabetically so a diff stays readable. Add a new entry here in
 * the same change that adds it to the BE emitter and the FE enum.
 */
const KNOWN_BE_EVENT_TYPES = [
  "agent.responded",
  "command.run",
  // P0-5 — account-scoped consent transitions. The backend emits these to
  // its dedicated ``consent_events`` table, not the per-session supervision
  // stream, but they share the same discriminated union on the FE.
  "consent.granted",
  "consent.revoked",
  "context.selected",
  "diff.hovered",
  "diff.opened",
  "file.edited",
  "file.reverted",
  "patch.applied",
  "patch.failed",
  "patch.proposed",
  "prompt.submitted",
  "session.abandoned",
  "session.errored",
  "session.gave_up",
  "session.provision_failed",
  "session.started",
  "submission.failed",
  "submission.graded",
  "submission.requested",
  "test.run",
  "tutorial.completed",
  "tutorial.dismissed",
  "tutorial.step_completed",
  "validator.flag",
] as const;

describe("supervision event-type contract", () => {
  it("every FE enum value is in the BE-known list", () => {
    const feValues = Object.values(SupervisionEventType).sort();
    const beValues = [...KNOWN_BE_EVENT_TYPES].sort();
    const missingFromBe = feValues.filter((v) => !beValues.includes(v as never));
    expect(missingFromBe).toEqual([]);
  });

  it("every BE-known value is in the FE enum (forces an FE update on BE additions)", () => {
    const feValues = new Set<string>(Object.values(SupervisionEventType));
    const missingFromFe = KNOWN_BE_EVENT_TYPES.filter((v) => !feValues.has(v));
    expect(missingFromFe).toEqual([]);
  });

  it("the FE enum has no duplicate string values", () => {
    const values = Object.values(SupervisionEventType);
    expect(new Set(values).size).toBe(values.length);
  });
});
