import * as React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import type { AgentTurn } from "@arena/shared-types";

import { AgentChat } from "@/components/workspace/AgentChat";

const TURN: AgentTurn = {
  id: "turn-1",
  session_id: "session-1",
  turn_index: 0,
  user_prompt: "Please fix the auth bug.",
  selected_context: { files: [], logs: [], tests: [], extras: [] },
  agent_response: "Sure — applying a patch.",
  proposed_actions: ["apply_patch"],
  applied_patch: null,
  patch_applied_at: null,
  created_at: "2026-05-21T10:00:00Z",
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe("AgentChat wiring", () => {
  it("calls onSubmit with the trimmed prompt", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(
      <AgentChat
        turns={[]}
        contextPaths={[]}
        onSubmit={onSubmit}
      />
    );
    const textarea = screen.getByLabelText(/prompt the agent/i);
    fireEvent.change(textarea, {
      target: { value: "  Investigate the cookie check.  " },
    });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit).toHaveBeenCalledWith("Investigate the cookie check.");
  });

  it("renders an Apply Patch button per turn and calls onApplyPatch with the turn id", async () => {
    const onApplyPatch = vi.fn().mockResolvedValue(undefined);
    render(
      <AgentChat
        turns={[TURN]}
        contextPaths={[]}
        onApplyPatch={onApplyPatch}
      />
    );

    const applyBtn = screen.getByRole("button", { name: /apply patch/i });
    fireEvent.click(applyBtn);

    await waitFor(() => expect(onApplyPatch).toHaveBeenCalledTimes(1));
    expect(onApplyPatch).toHaveBeenCalledWith("turn-1");
  });

  it("does not show Apply Patch once the turn has been applied", () => {
    const applied: AgentTurn = {
      ...TURN,
      applied_patch: "diff --git a/x b/x",
      patch_applied_at: "2026-05-21T10:01:00Z",
    };
    render(
      <AgentChat
        turns={[applied]}
        contextPaths={[]}
        onApplyPatch={vi.fn()}
      />
    );
    expect(
      screen.queryByRole("button", { name: /apply patch/i })
    ).not.toBeInTheDocument();
    expect(screen.getByText(/Patch applied\./i)).toBeInTheDocument();
  });
});
