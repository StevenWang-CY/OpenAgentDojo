/**
 * Reports + submission contract tests.
 *
 * Asserts `getReport` and `getSubmission` parse the rich Submission shape
 * that the grader emits — including the narrowed `score_report`,
 * `validator_results`, and per-test arrays.
 */
import { describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import type { ScoreReport, Submission, ValidatorResult } from "@arena/shared-types";
import { getReport, getSubmission } from "@/lib/api";
import { API_BASE, expectShape, withContractServer } from "./_setup";

const sessionId = "44444444-4444-4444-4444-444444444444";
const submissionId = "55555555-5555-5555-5555-555555555555";

const scoreReport: ScoreReport = {
  total: 78,
  dimensions: {
    final_correctness: { score: 24, max: 30, signals: ["3/4 hidden tests passed"] },
    verification: { score: 14, max: 15, signals: ["ran auth tests"] },
    agent_review: { score: 11, max: 15, signals: ["diff opened"] },
    prompt_quality: { score: 7, max: 10, signals: ["mentions regression test"] },
    context_selection: { score: 8, max: 10, signals: ["selected middleware + session.ts"] },
    safety: { score: 9, max: 10, signals: ["no validation removed"] },
    diff_minimality: { score: 5, max: 10, signals: ["12 lines added"] },
  },
  strengths: ["Right context up front"],
  weaknesses: ["Did not run typecheck"],
  missed_failure_mode: false,
  badges_earned: ["regression-test-writer"],
};

const validatorResults: ValidatorResult[] = [
  {
    kind: "diff_scope",
    passed: true,
    violations: [],
    penalty: 0,
    evidence: [],
  },
  {
    kind: "forbidden_changes",
    passed: false,
    violations: ["removed assertOwnerOrAdmin"],
    penalty: 10,
    evidence: [{ file: "settings.ts", line: 42, snippet: "// guard removed" }],
  },
];

const submission: Submission = {
  id: submissionId,
  session_id: sessionId,
  final_diff: "diff --git ...",
  visible_test_results: [
    {
      suite: "unit",
      exit_code: 0,
      stdout: "1 passed",
      stderr: "",
      passed: 1,
      failed: 0,
      skipped: 0,
    },
  ],
  hidden_test_results: [
    {
      suite: "hidden-auth",
      exit_code: 1,
      stdout: "0 passed, 1 failed",
      stderr: "",
      passed: 0,
      failed: 1,
      skipped: 0,
    },
  ],
  validator_results: validatorResults,
  score_report: scoreReport,
  total_score: 78,
  created_at: "2025-05-21T11:00:00Z",
  ideal_solution: "# Reference fix\n…",
  ideal_solution_diff: "--- a/auth.ts\n+++ b/auth.ts",
  agent_patch_diff: "--- a/auth.ts\n+++ b/auth.ts",
  critical_moments: [],
};

withContractServer([
  http.get(`${API_BASE}/api/v1/reports/:id`, ({ params }) => {
    if (params.id !== submissionId) {
      return HttpResponse.json({ detail: "not found" }, { status: 404 });
    }
    return HttpResponse.json(submission);
  }),
  http.get(`${API_BASE}/api/v1/sessions/:id/submission`, () =>
    HttpResponse.json(submission)
  ),
]);

describe("reports contract", () => {
  it("GET /reports/{id} parses the full Submission shape", async () => {
    const r = await getReport(submissionId);
    expectShape(r as unknown as Record<string, unknown>, [
      "id",
      "session_id",
      "final_diff",
      "visible_test_results",
      "hidden_test_results",
      "validator_results",
      "score_report",
      "total_score",
      "created_at",
    ]);
    expect(r.total_score).toBe(78);
    expect(r.score_report.total).toBe(78);
    expect(r.score_report.dimensions.final_correctness.score).toBe(24);
    expect(r.validator_results[0]!.kind).toBe("diff_scope");
  });

  it("GET /sessions/{id}/submission returns the same Submission shape", async () => {
    const s = await getSubmission(sessionId);
    expect(s.id).toBe(submissionId);
    expect(s.hidden_test_results[0]!.passed).toBe(false);
    expect(s.validator_results[1]!.evidence?.[0]?.file).toBe("settings.ts");
  });

  it("surfaces 404 as ApiError", async () => {
    await expect(getReport("does-not-exist")).rejects.toMatchObject({
      status: 404,
    });
  });
});
