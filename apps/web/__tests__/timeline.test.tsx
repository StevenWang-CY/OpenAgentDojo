import * as React from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { SupervisionEvent } from "@arena/shared-types";
import { Timeline } from "@/components/workspace/Timeline";

const SESSION = "11111111-2222-3333-4444-555555555555";

function event<T extends SupervisionEvent["event_type"]>(
  id: number,
  event_type: T,
  payload: Extract<SupervisionEvent, { event_type: T }>["payload"],
  occurred_at = "2026-05-21T10:00:00.000Z"
): SupervisionEvent {
  return {
    id,
    session_id: SESSION,
    event_type,
    payload,
    occurred_at,
  } as SupervisionEvent;
}

describe("Timeline rendering — Phase 4.A contract", () => {
  it("shows an empty-state when there are no events", () => {
    render(<Timeline events={[]} />);
    expect(
      screen.getByText(/supervision timeline will appear here/i)
    ).toBeInTheDocument();
  });

  it("renders prompt.submitted with the prompt text and char_count", () => {
    render(
      <Timeline
        events={[
          event(1, "prompt.submitted", {
            turn_index: 0,
            text: "Please scan auth.ts for the cookie bug.",
            char_count: 39,
          }),
        ]}
      />
    );
    expect(screen.getByText(/Prompt #1/)).toBeInTheDocument();
    expect(screen.getByText(/scan auth\.ts/)).toBeInTheDocument();
    expect(screen.getByText(/39 chars/)).toBeInTheDocument();
  });

  it("renders agent.responded using turn_index + response_summary", () => {
    render(
      <Timeline
        events={[
          event(2, "agent.responded", {
            turn_index: 1,
            response_summary: "I'll patch the expiration check in session.ts.",
            intent: "fix",
            llm_used: true,
          }),
        ]}
      />
    );
    expect(screen.getByText(/Agent responded #2/)).toBeInTheDocument();
    expect(screen.getByText(/patch the expiration check/)).toBeInTheDocument();
  });

  it("renders patch.proposed with the patch_file path", () => {
    render(
      <Timeline
        events={[
          event(3, "patch.proposed", {
            turn_index: 0,
            patch_file: "patches/turn-0.patch",
            intent: "fix",
          }),
        ]}
      />
    );
    expect(screen.getByText(/Patch proposed #1/)).toBeInTheDocument();
    expect(screen.getByText("patches/turn-0.patch")).toBeInTheDocument();
  });

  it("renders patch.applied with file_count / added / removed", () => {
    render(
      <Timeline
        events={[
          event(4, "patch.applied", {
            turn_index: 0,
            file_count: 3,
            added: 12,
            removed: 4,
          }),
        ]}
      />
    );
    expect(
      screen.getByText("3 files · +12 / -4")
    ).toBeInTheDocument();
  });

  it("renders patch.failed with the error and optional counts", () => {
    render(
      <Timeline
        events={[
          event(5, "patch.failed", {
            turn_index: 0,
            error: "merge conflict in foo.ts",
            file_count: 1,
            added: 0,
            removed: 0,
          }),
        ]}
      />
    );
    expect(screen.getByText("Patch failed")).toBeInTheDocument();
    expect(
      screen.getByText(/merge conflict in foo\.ts \(1 files · \+0 \/ -0\)/)
    ).toBeInTheDocument();
  });

  it("renders patch.failed without counts when omitted", () => {
    render(
      <Timeline
        events={[
          event(6, "patch.failed", {
            turn_index: 0,
            error: "patch could not be parsed",
          }),
        ]}
      />
    );
    expect(screen.getByText("patch could not be parsed")).toBeInTheDocument();
  });

  it("renders submission.graded total and a breakdown chart when populated", () => {
    render(
      <Timeline
        events={[
          event(7, "submission.graded", {
            score: 82,
            breakdown: {
              final_correctness: { score: 28, max: 30, signals: [] },
              verification: { score: 18, max: 20, signals: [] },
            },
          }),
        ]}
      />
    );
    expect(screen.getByText("Total 82 / 100")).toBeInTheDocument();
    expect(screen.getByTestId("timeline-breakdown")).toBeInTheDocument();
    expect(screen.getByText("final_correctness")).toBeInTheDocument();
    expect(screen.getByText("verification")).toBeInTheDocument();
  });

  it("renders submission.graded without breakdown chart when empty", () => {
    render(
      <Timeline
        events={[
          event(8, "submission.graded", {
            score: 50,
            breakdown: {},
          }),
        ]}
      />
    );
    expect(screen.getByText("Total 50 / 100")).toBeInTheDocument();
    expect(screen.queryByTestId("timeline-breakdown")).not.toBeInTheDocument();
  });

  it("renders submission.failed with stage and detail", () => {
    render(
      <Timeline
        events={[
          event(9, "submission.failed", {
            stage: "scoring",
            detail: "rubric weights summed to 101",
          }),
        ]}
      />
    );
    expect(screen.getByText("Submission failed")).toBeInTheDocument();
    expect(
      screen.getByText("scoring: rubric weights summed to 101")
    ).toBeInTheDocument();
  });

  it("renders session.errored with stage and detail", () => {
    render(
      <Timeline
        events={[
          event(10, "session.errored", {
            stage: "provisioning",
            detail: "Docker daemon not reachable",
          }),
        ]}
      />
    );
    expect(screen.getByText("Session errored")).toBeInTheDocument();
    expect(
      screen.getByText("provisioning: Docker daemon not reachable")
    ).toBeInTheDocument();
  });

  it("renders session.abandoned with a default reason when omitted", () => {
    render(
      <Timeline events={[event(11, "session.abandoned", {})]} />
    );
    expect(screen.getByText("Session abandoned")).toBeInTheDocument();
    expect(screen.getByText("Session reaped")).toBeInTheDocument();
  });

  it("renders session.abandoned with the supplied reason", () => {
    render(
      <Timeline
        events={[
          event(12, "session.abandoned", { reason: "idle_timeout" }),
        ]}
      />
    );
    expect(screen.getByText("idle_timeout")).toBeInTheDocument();
  });

  it("renders diff.opened with (workspace) when path is empty", () => {
    render(
      <Timeline events={[event(13, "diff.opened", { path: "" })]} />
    );
    expect(screen.getByText("(workspace)")).toBeInTheDocument();
  });

  it("renders diff.hovered with ? when line is omitted", () => {
    render(
      <Timeline
        events={[event(14, "diff.hovered", { path: "app/main.ts" })]}
      />
    );
    expect(screen.getByText("app/main.ts:?")).toBeInTheDocument();
  });
});
