/**
 * Visible keyword groups surfaced as prompt-hint chips in `AgentChat`.
 * These are intentionally *advisory* — the real intent classifier lives on the
 * server (apps/api/app/agent/intents.py). Keeping the list visible to the user
 * helps teach high-leverage supervision habits per IMPLEMENTATION_PLAN.md §11.2.4.
 */
export interface IntentKeywordGroup {
  id: "investigate" | "scope" | "test" | "review" | "safety";
  label: string;
  description: string;
  keywords: string[];
}

export const INTENT_KEYWORD_GROUPS: IntentKeywordGroup[] = [
  {
    id: "investigate",
    label: "Investigate",
    description: "Ask the agent to find the root cause, not just patch symptoms.",
    keywords: ["reproduce", "root cause", "investigate", "explain"],
  },
  {
    id: "scope",
    label: "Constrain scope",
    description: "Tell the agent which files to touch — and which to leave alone.",
    keywords: ["minimal", "do not modify", "only change", "without changing"],
  },
  {
    id: "test",
    label: "Demand regression",
    description: "Require a failing test before any fix lands.",
    keywords: ["regression test", "add a test", "failing test first"],
  },
  {
    id: "review",
    label: "Force review",
    description: "Make the agent surface trade-offs and edge cases.",
    keywords: ["edge case", "trade-off", "what could break", "side effects"],
  },
  {
    id: "safety",
    label: "Safety",
    description: "Forbid risky moves the agent is prone to make.",
    keywords: ["no new dependencies", "do not remove validation", "expiration"],
  },
];
