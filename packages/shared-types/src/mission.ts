// Hand-authored until CI regenerates from openapi.json.
// Frontend-visible subset of the mission manifest (IMPLEMENTATION_PLAN.md §7.1).
// The backend owns the full manifest; only fields needed by the UI are exposed here.

export interface MissionRepoInfo {
  pack: string;
  language_runtime: "node20" | "python312";
  workdir: string;
  visible_test_commands: {
    unit?: string;
    integration?: string;
    typecheck?: string;
    lint?: string;
  };
}

export interface MissionFailureMode {
  id: string;
  title: string;
  description: string;
}

export interface MissionScoringWeights {
  final_correctness: 30;
  verification: 20;
  agent_review: 15;
  prompt_quality: 10;
  context_selection: 10;
  safety: 10;
  diff_minimality: 5;
}
