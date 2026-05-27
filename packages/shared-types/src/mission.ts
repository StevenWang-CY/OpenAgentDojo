// Hand-authored until CI regenerates from openapi.json.
// Frontend-visible subset of the mission manifest (IMPLEMENTATION_PLAN.md §7.1).
// The backend owns the full manifest; only fields needed by the UI are exposed here.

export interface MissionRepoInfo {
  pack: string;
  // P1-1 — ``go122`` is the third sandbox runtime shipped with the
  // ``go-orders-service`` repo pack. Kept in lockstep with the backend
  // ``app.missions.manifest.LanguageRuntime`` Literal.
  language_runtime: "node20" | "python312" | "go122";
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
