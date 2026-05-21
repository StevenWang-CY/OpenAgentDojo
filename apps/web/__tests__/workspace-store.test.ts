import { beforeEach, describe, expect, it } from "vitest";
import { useWorkspaceStore } from "@/stores/workspaceStore";
import type { AgentTurn, SupervisionEvent } from "@arena/shared-types";

const SESSION = "11111111-2222-3333-4444-555555555555";

function get() {
  return useWorkspaceStore(SESSION).getState();
}

describe("workspaceStore", () => {
  beforeEach(() => {
    localStorage.clear();
    // Reset the store to defaults so persistence from a prior test doesn't bleed in.
    get().reset();
  });

  it("toggles context paths idempotently", () => {
    const s = get();
    s.toggleContextPath("backend/auth/session.ts");
    expect(get().selectedContext).toEqual(["backend/auth/session.ts"]);
    s.toggleContextPath("backend/auth/session.ts");
    expect(get().selectedContext).toEqual([]);
  });

  it("opens a file and closes the tab", () => {
    const s = get();
    s.openFile("a.ts");
    s.openFile("b.ts");
    expect(get().openTabs).toEqual(["a.ts", "b.ts"]);
    expect(get().activeFile).toBe("b.ts");
    s.closeTab("b.ts");
    expect(get().openTabs).toEqual(["a.ts"]);
    expect(get().activeFile).toBe("a.ts");
  });

  it("buffers active-file content per path", () => {
    const s = get();
    s.openFile("a.ts");
    s.setActiveFileContent("a.ts", "console.log(1);");
    expect(get().fileBuffers["a.ts"]).toBe("console.log(1);");
  });

  it("pushes and dedupes agent turns by turn_index", () => {
    const turn: AgentTurn = {
      id: "t1",
      session_id: SESSION,
      turn_index: 0,
      user_prompt: "fix the bug",
      selected_context: { files: [], logs: [], tests: [], extras: [] },
      agent_response: "I'll look at session.ts.",
      proposed_actions: ["apply_patch"],
      applied_patch: null,
      patch_applied_at: null,
      created_at: new Date().toISOString(),
    };
    const s = get();
    s.pushAgentTurn(turn);
    s.pushAgentTurn({ ...turn, agent_response: "edited" });
    expect(get().agentTurns).toHaveLength(1);
    expect(get().agentTurns[0]!.agent_response).toBe("edited");
  });

  it("appends supervision events in order", () => {
    const event: SupervisionEvent = {
      id: 1,
      session_id: SESSION,
      event_type: "session.started",
      payload: { mission_id: "m", initial_commit: "abc1234" },
      occurred_at: new Date().toISOString(),
    };
    get().pushEvent(event);
    expect(get().events).toHaveLength(1);
    expect(get().events[0]!.event_type).toBe("session.started");
  });
});
