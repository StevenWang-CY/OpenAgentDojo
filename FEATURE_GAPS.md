# OpenAgentDojo — Feature Gap Analysis

A grounded, opinionated audit of where the shipped product falls short of its
own stated goal and target audience. Written after a full walkthrough of
[README.md](README.md), [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md),
[CONTEXT.md](CONTEXT.md), the marketing surface, the workspace, the grading
engine, the mission catalog, and the API.

Every gap below has been pressure-tested against the same acid test:

> **Would the product fail at its stated goal — or fail to serve a real target
> user — if this never shipped?**

Things that pass that test land in [P0](#p0--blocks-the-stated-goal). Things
that are load-bearing for *full* goal alignment but tolerable to defer land in
[P1](#p1--necessary-for-full-goal-alignment). The
[P2](#p2--recommended-but-not-blocking) list is intentionally short. Anything
the [IMPLEMENTATION_PLAN §26](IMPLEMENTATION_PLAN.md) declares out-of-scope
(LLM-on-grading, native mobile, multiplayer, full i18n, browser-based code
execution, user-authored missions) is **not** flagged as a gap.

---

## 1. What the product claims to be

From the README and CONTEXT:

- **Product.** A browser-based simulator that teaches developers to *supervise*
  AI coding agents inside real repositories.
- **Differentiator.** Grades the **process** of supervision (prompting,
  context selection, diff review, verification, correction, safety,
  minimality) — not just the final patch.
- **Pedagogical thesis.** "Patches that look right, aren't. Train the eye that
  catches them." Process supervision is a learnable skill.
- **Determinism promise.** Grading is a pure function of the supervision event
  log + prompt-judgement cache; replays are byte-identical.
- **Shipping aspiration** ([IMPLEMENTATION_PLAN §17 M7](IMPLEMENTATION_PLAN.md)):
  "a landing page strong enough for a recruiter cold-open" and a "shareable
  skill profile."

## 2. Who the product is for

These users are inferred from the README copy, the existence of a public
shareable profile, the language of the rubric, and the rate-limit / sandbox
posture:

1. **Practicing engineers who already use AI coding agents** (Copilot, Cursor,
   Claude Code, Aider, Continue). They want to upgrade from "vibe-checking
   the diff" to a disciplined review habit. *Primary*.
2. **Tech leads / hiring managers** evaluating whether a candidate (or their
   own team) supervises agents responsibly. *Secondary, load-bearing for the
   "shareable profile" claim.*
3. **Bootcamp and cohort-based learners** ramping into agentic workflows.
   *Tertiary; same product surface, different motivation.*
4. **Recruiters** consuming a candidate's supervision profile as a hiring
   signal. *Indirect — they don't sign in, but the profile must read as
   credible to them.*

If the *primary* audience is wrong, several priorities below shift; see
[§7 Open product questions](#7-open-product-questions-the-team-must-answer).

## 3. Verdict in one paragraph

The MVP is technically coherent and architecturally honest. The determinism
posture, the event-sourced supervision log, the 7-dimension rubric with hard
caps and floors, the cached prompt judge with rubric-version bumping, and the
rootless-Docker sandbox are all genuinely good. **The product, however, is
not yet sufficient for any of its target users to actually achieve the stated
goal.** It *measures* supervision skill but does very little to *teach* it; it
runs out of content in one evening; it claims a credentialed profile without
identity verification, anti-cheating posture, or exportable artifacts; and it
collects PII without a Privacy Policy. The gap list below is what closes those
holes without inflating scope.

---

## P0 — Blocks the stated goal

Each item here is necessary for the product to deliver on its core promise to
its primary user. None of them is on the explicit out-of-scope list. None is a
nice-to-have.

### P0-1. In-product onboarding / "Mission 00" tutorial

**The gap.** A first-time user lands in the workspace (file tree, Monaco,
diff, terminal, agent chat, brief, signals, timeline — eight surfaces) with
no guided walkthrough. The empty state in
[AgentChat.tsx:244](apps/web/components/workspace/AgentChat.tsx#L244) helps,
but the user does not know that selecting context files is *itself* scored,
that opening the diff is a *required* habit, or that `Cmd/Ctrl+Enter` submits.

**Why necessary.** The product's pedagogical claim is "train the eye." The
training cannot start if the user can't navigate the dojo. The 7-dimension
rubric will penalize a confused user for skipping behaviors they didn't know
existed.

**Acceptance criteria.**
- A first run launches a guided `mission-00-orientation` (or coachmark
  overlay) covering: pick context → prompt → apply patch → open diff →
  verify → submit.
- The catalog visually marks Mission 00 as "Start here."
- A "Replay tutorial" link sits in the user dropdown.

### P0-2. Mission post-mortem walkthrough — *not* a static markdown blob

**The gap.** When a user misses the failure mode, the report renders
[`ideal_solution.md`](missions/01-auth-cookie-expiration/) as a markdown
section ([ReportView.tsx:203](apps/web/components/report/ReportView.tsx#L203)).
There is no inline contrast between *what the user submitted* and *what the
ideal solution looks like*; no highlighting of the specific line they should
have caught; no scrub through the timeline showing the moment they could have
pushed back.

**Why necessary.** The product positions itself as *training*, not assessment.
A test that hands you the answer key in a separate document teaches very
little — that is precisely the supervision gap the product exists to close.
Without this, the platform measures and grades but does not actually train
the eye it claims to train.

**Acceptance criteria.**
- Report includes a "**What the agent did vs. what you should have caught**"
  side-by-side diff (user-final vs. ideal) with the load-bearing line
  highlighted.
- For missed failure modes, the report scrubs to the supervision event where
  intervention was still cheap and shows: "you had 12 seconds between
  `patch.applied` and `submission.requested`."
- Strengths/weaknesses items link to the specific timeline event they
  reference (the data is in `supervision_events` already).

### P0-3. Replay/retry mechanic with a resolved multi-attempt policy

**The gap.** [OQ-0004](docs/open-questions.md) (best vs. latest vs. both) is
still open. There is no first-class "Retry mission" CTA. The
WorkspaceShell's "abandoned" state has a "Restart mission" link but does not
position retrying as the natural learning loop. The
`/missions/{id}` detail page does not show "you have attempted this 2× — best
72 — last 65."

**Why necessary.** Training requires repetition. A platform that
prominently shows your score once and quietly hides re-attempts is signaling
that re-attempts are second-class — which contradicts its own goal.

**Acceptance criteria.**
- OQ-0004 is resolved with an ADR and documented in the user-visible help.
- Per-mission attempt history surfaces on both `/missions/{id}` (private)
  and `/profile/{handle}` (public, summarized — best + delta, never the
  count, per the resolved policy).
- The report ends with "Retry this mission" alongside "Next mission."
- Multi-attempt scoring is explicitly stated *in the report itself* so users
  understand whether they're being judged on best or latest.

### P0-4. "Give up" with capped ideal-solution reveal

**The gap.** [OQ-0002](docs/open-questions.md) is still open and there is no
"give up" affordance in `WorkspaceShell.tsx`. Frustrated users abandon and
learn nothing. The plan itself recognized this risk.

**Why necessary.** Same reason as the post-mortem: a teaching product must
not punish curiosity. The plan's proposed score cap of 50 (soft-blocked for
the first 10 minutes) is a reasonable compromise; ship it.

**Acceptance criteria.**
- After ≥10 minutes in a session, a "Reveal ideal solution (caps your score
  at 50)" affordance appears in the workspace top bar.
- Using it emits a `session.gave_up` supervision event and forces submit
  immediately afterward, so the timeline is honest.
- The profile shows the attempt with a `gave_up` chip — no hiding.

### P0-5. Legal pages — Terms of Service, Privacy Policy, Cookie consent

**The gap.** There is no `/privacy`, no `/terms`, no cookie-consent surface
in `apps/web/app/(marketing)/`. The footer
([Footer.tsx](apps/web/components/marketing/Footer.tsx)) links to Missions,
Sign-in, GitHub, and Status — no legal pages. The product collects email
(magic link), display name, session cookies, behavioral telemetry
([TelemetryProvider.tsx](apps/web/components/TelemetryProvider.tsx)), and
sandbox-execution metadata.

**Why necessary.** Collecting PII and behavioral data without these pages is
illegal under GDPR (EU/UK), CCPA/CPRA (California), PIPEDA (Canada), the UK
DPA, and several others. This is non-optional regardless of audience.

**Acceptance criteria.**
- `/legal/terms`, `/legal/privacy`, `/legal/cookies` exist and are linked
  from the marketing footer, the in-app header dropdown, and the sign-in
  page footer.
- The Privacy Policy enumerates exactly what is stored (email, handle,
  supervision events, prompts, command output) and the retention window.
- A cookie banner gates non-essential telemetry on first visit
  (essential-only by default); the choice is remembered in `localStorage`.

### P0-6. Account self-service — change email, export data, delete account

**The gap.** No `/account` or `/settings` route exists. The user cannot
change their email, sign out of other devices, export their data, or delete
their account. The auth flow is magic-link-only, so a user whose email is
compromised has no remediation path beyond "email support" (which doesn't
exist either).

**Why necessary.** GDPR Article 15 (right of access), Article 17 (right to
erasure), and Article 20 (right to data portability) are not optional for any
service that processes EU residents' data. CCPA Section 1798.105 mirrors the
deletion requirement for Californians. Beyond compliance, this is table
stakes for any product that touches the word "profile."

**Acceptance criteria.**
- `/account` shows: email + change, sign-out everywhere, "Export my data"
  (zips up `users` + `sessions` + `submissions` + `supervision_events`),
  and "Delete my account" (hard-delete after a 7-day grace).
- The deletion path tombstones `users.email` and hard-deletes
  `supervision_events` rows for the user; profile becomes a 404.
- A confirmation email gates deletion (same magic-link primitive).

### P0-7. Identity verification — GitHub OAuth, not "optional / post-MVP"

**The gap.** [IMPLEMENTATION_PLAN.md §16](IMPLEMENTATION_PLAN.md) and
[docs/security.md](docs/security.md) both list GitHub OAuth as "optional /
post-MVP." It is not wired in
[apps/api/app/auth/](apps/api/app/auth/) or the sign-in page. Magic-link-only
means any random person can claim a handle that *looks* like a known engineer
and present it as a credential.

**Why necessary.** The README explicitly markets a "shareable skill profile"
and the implementation plan aspires to "strong enough for a recruiter
cold-open." A profile with no identity verification is not a credential — it
is a self-attestation. Recruiters will not trust it; the claim collapses.

**Acceptance criteria.**
- GitHub OAuth is a primary sign-in option, not a post-hoc link.
- The profile page renders a verified-via-GitHub badge with a link to the
  GitHub profile when present.
- Email-only profiles render an explicit "Self-attested · not GitHub-linked"
  chip so consumers can calibrate trust.

### P0-8. Anti-cheating posture for the credentialed mode

**The gap.** Nothing prevents a user from opening ChatGPT, Cursor, or Claude
in another tab during a mission. There is no proctoring, no tab-blur signal,
no "honor mode" badge. The grading rubric explicitly measures *prompt
quality* — which is trivially gamed by pasting an LLM-rewritten prompt.

**Why necessary.** If the profile is a credential (per P0-7), the score must
be defensible. Without integrity signals, every score on every profile is
indistinguishable from cheating, which destroys the signal for the
secondary/recruiter audience.

**Acceptance criteria.**
- Every session ships in *self-study mode* by default with a visible
  banner: "Honor mode — practice only, not a verified score."
- A *proctored mode* toggle (opt-in per attempt) emits `tab.blurred`,
  `paste.large`, and `focus.lost` events; the resulting submission carries
  a `verified` flag.
- The profile shows the proctored-attempt subset separately. Honor-mode
  scores never appear on the public radar averages.

### P0-9. Find-in-files / repo-wide search in the workspace

**The gap.** Monaco's built-in per-file find (`Cmd+F`) works because the
editor is Monaco. But there is no cross-file search wired into
[CodeEditor.tsx](apps/web/components/workspace/CodeEditor.tsx) or the
FileTree, and no `Cmd+P` quick-open. The `Sandbox.run` API does ship
`ripgrep` in the base image
([IMPLEMENTATION_PLAN.md §9.1](IMPLEMENTATION_PLAN.md)) but no UI hits it.

**Why necessary.** The product's claim is "real repositories." A real repo is
not navigable without find-in-files. Supervising a 200-file repo by clicking
through the tree is not a fair test of supervision skill — it tests patience.

**Acceptance criteria.**
- `Cmd+P` / `Ctrl+P` opens a quick-open file picker scoped to the sandbox
  workspace.
- `Cmd+Shift+F` / `Ctrl+Shift+F` opens a global ripgrep panel
  (server-backed; results stream over the existing WS channel).
- Both surfaces are documented in a `Help (?)` overlay.

### P0-10. Email deliverability fallback

**The gap.** Magic-link-only auth is brittle. Corporate spam filters,
GMail's Promotions tab, and Outlook's Focused/Other split silently swallow
sign-in links every day. The sign-in page
([apps/web/app/auth/sign-in/page.tsx](apps/web/app/auth/sign-in/page.tsx))
says "Check your inbox" and stops there — no resend, no troubleshooting, no
alternate path.

**Why necessary.** A user who never receives the link cannot use the
product. This is the most common, lowest-effort, highest-frequency abandonment
path for magic-link products.

**Acceptance criteria.**
- "Didn't get the link?" with a 60-second resend timer and a troubleshooting
  blurb (spam/promotions/corporate filters).
- A GitHub-OAuth fallback (rolls naturally into P0-7) so users who simply
  cannot receive the email have a working path.
- The magic-link email's sender reputation is monitored (deliverability is
  an ongoing concern, not a one-shot fix).

### P0-11. Exportable / verifiable report artifact (PDF + signed permalink)

**The gap.** The "Share report" button mints a 30-day JWT-bearing URL
([ReportView.tsx:241](apps/web/components/report/ReportView.tsx#L241)). That
URL expires. There is no PDF, no PNG, no signed permanent artifact. A
candidate cannot put a report on a résumé.

**Why necessary.** The README markets the "shareable skill profile" *and* the
report. A shareable thing that expires in 30 days and lives only as HTML is
not shareable in the credentialing sense.

**Acceptance criteria.**
- "Download PDF" produces a static PDF of the report with the score, radar,
  dimension breakdown, badges, the proctored/honor-mode flag, and a
  verification footer (`openagentdojo.app/verify/{submission_id}`).
- The verification URL is permanent and renders a minimal page proving the
  submission exists, when it was graded, and the score — without exposing
  the full event log.
- The Open Graph image for the report
  ([opengraph-image.tsx](apps/web/app/(app)/report/[submissionId]/opengraph-image.tsx))
  is also downloadable as a PNG for LinkedIn-style sharing.

### P0-12. Reset-to-initial / clean session restart

**The gap.** `POST /sessions/{id}/files/revert` reverts a single file. There
is no "abandon this attempt cleanly and start over" affordance short of
abandoning the entire session, which loses every event the user *did* learn
from. Users will not realize a wrong-turn is recoverable.

**Why necessary.** Real supervision involves backtracking. A platform that
penalizes backtracking (or makes it implicit and lossy) teaches users to not
explore — the opposite of the stated goal.

**Acceptance criteria.**
- A "Reset workspace to initial commit" affordance in the WorkspaceTopBar,
  guarded by a confirm dialog ("this discards your file edits; your
  supervision timeline stays").
- The reset is recorded as a `session.reset` event so the grader can see how
  many times the user backed off.

### P0-13. Legal/license + contributor sanity at the repo level

**The gap.** The repo has no `LICENSE` file. The README says "Internal MVP —
not for redistribution," but the project is public on GitHub and has an
`open-source-style` ADR convention. There is no `CONTRIBUTING.md`. The
[IMPLEMENTATION_PLAN.md §11.1](IMPLEMENTATION_PLAN.md) lists verification=20,
diff_minimality=5; the shipped code in
[`apps/api/app/grading/dimensions.py`](apps/api/app/grading/dimensions.py)
ships 15/10. The plan drifted from reality.

**Why necessary.** A public repo without a license is "all rights reserved"
by default, which contradicts the open posture of every other file. A
documentation source-of-truth that disagrees with the runtime is worse than
no documentation.

**Acceptance criteria.**
- Add an explicit `LICENSE` file (or change the README's "open-source"
  framing).
- `CONTRIBUTING.md` covers the mission-authoring flow and points to the
  schema validators.
- Reconcile IMPLEMENTATION_PLAN §11 with `dimensions.py` (either back-port
  the plan to 15/10 or restore code to 20/5; the resolved values must be in
  one place only).

---

## P1 — Necessary for full goal alignment

These don't block the launch but they are load-bearing for the product to
actually accomplish what it claims.

### P1-1. Expand the mission catalog (volume + diversity)

10 missions is a one-evening exhaust. After that, the product offers
**zero new training value** to a returning user. Either ship more missions or
publish a visible roadmap with dated placeholders so users know what's coming.
Critically, only **2 repo packs** back all 10 missions
([missions/README.md](missions/README.md)) — the "real repository" claim
needs at least a third pack representing a different stack (e.g. a
frontend-only React app, a Go microservice, a Rails monolith).

### P1-2. Adaptive next-mission engine

`feedback_narrative[].recommended_mission_ids` exists already
([reports schema](docs/schemas/score_report.schema.json)). The report links
to a recommended next mission (good!) but the profile and the catalog do not
*surface* those recommendations as a learning path. The `/skills` page is
inventory, not pedagogy.

Acceptance: the profile shows "your weakest dimension is `agent_review` —
try these three missions in order." Catalog highlights the recommended
next mission for the signed-in user with a `// recommended` chip.

### P1-3. LSP / IntelliSense in Monaco for sandbox languages

[CodeEditor.tsx](apps/web/components/workspace/CodeEditor.tsx) wires Monaco
without an LSP, so editing in-product is meaningfully worse than editing in
VS Code. For a tool whose USP is "real repositories," this undermines the
core experience. Worker-side TypeScript and Python language services exist
as off-the-shelf packages; wire them through the sandbox.

### P1-4. Workspace notes / scratchpad

A supervisor's habit of *jotting reasoning before prompting* is itself a
supervision skill worth measuring. Today there's nowhere to put a thought
short of pasting it into the agent prompt (which conflates "thinking" with
"asking"). A persistent scratchpad pane (saved as `session_notes` rows)
unlocks both a habit and a future scoring signal.

### P1-5. User-vs-ideal diff side-by-side in the report

A natural P1 derivative of P0-2. Once the post-mortem exists, render it as a
proper side-by-side diff (the `DiffViewer` component already supports it)
rather than two separate markdown blobs.

### P1-6. Supervision-event JSON export ("the replay artifact")

[CONTEXT.md](CONTEXT.md) and the grading docs emphasize *replayability*:
re-running the grader against the same event stream produces the same score
report. Expose this as a downloadable artifact (`GET
/api/v1/submissions/{id}/replay.json`). It's both a candidate's proof-of-work
and the foundation of any future score-appeal flow.

### P1-7. Score-appeal / "explain this score" affordance

The dimension breakdown shows signals but not their *evidence*. Each signal
should link to the specific supervision event(s) that produced it. An
"explain this score" surface that walks dimension-by-dimension with citations
to event ids is the trust-build move for the credentialing claim.

### P1-8. Keyboard shortcuts cheatsheet + `Help (?)` overlay

There's a `Cmd+Enter` hint in [SubmitDialog.tsx:94](apps/web/components/workspace/SubmitDialog.tsx#L94)
and that's it. A `?`-triggered overlay with the shortcut table (file-open,
search, submit, apply, revert) is industry-table-stakes for any IDE-shaped
product.

### P1-9. Failure-mode taxonomy doc + linked theory

The 10 failure modes are well-chosen but they exist as ad-hoc strings
(`checks_presence_not_expiration`, `overfitted_visible_test`, …). Publish a
[`docs/failure-modes.md`](docs/) that explains each as a *taxonomy* of
supervision failures, with references to the real-world incidents/patterns
they represent. The `/skills` page links into this doc per failure mode.
This is what converts the product from "10 quizzes" to "a body of knowledge."

### P1-10. Calibration transparency

Each mission already carries `expected_diff_lines_p50` and
`acceptance.yaml` envelopes
([missions/_calibration/](missions/_calibration/)). Surface these in the UI:
the mission detail page shows "median supervisor scores 62 here; ideal
solution scores 92." This makes the grader's choices defensible and gives
returning users a sense of where they sit.

### P1-11. Telemetry consent / opt-out UI

[TelemetryProvider](apps/web/components/TelemetryProvider.tsx) exists and
[lib/telemetry.ts](apps/web/lib/telemetry.ts) emits events
(`report_viewed`, `prompt_submitted`, etc.). There is no in-product opt-out.
Tied to P0-5 (Cookie consent) but distinct from it: even consenting users
need a Settings toggle.

### P1-12. Team / cohort dashboard

For the platform to be sold as training to a company, "team supervisor's
weekly heatmap" is the artifact a manager will ask for. The plan does not
include this and it is fine to defer past MVP, but it should be on the
public roadmap so that prospective customers can map it to a quarter.

### P1-13. Submission rate-limit + active-session feedback in the UI

Plan §21: 3 submissions/user/hour, 1 active session. When the user hits
either limit the API returns a 429 / 409 with a JSON body
([sessions/router.py:163](apps/api/app/sessions/router.py#L163)). The
frontend should render: "You've used 3/3 submissions; resets in 47 min" and
the active-session conflict already has FE handling, but it's not surfaced on
the catalog page as a status indicator.

### P1-14. Accessibility audit results

[e2e/a11y.spec.ts](apps/web/e2e/a11y.spec.ts) exists; the WCAG 2.1 AA target
is documented (plan §13.6). Publish the actual audit results, fix any
critical Axe violations on the workspace, and add the WAVE / Axe badge to the
footer. The radar chart in
[ScoreRadar.tsx](apps/web/components/report/ScoreRadar.tsx) and the
color-only diff highlights in [DiffViewer.tsx](apps/web/components/workspace/DiffViewer.tsx)
are the high-risk surfaces.

### P1-15. Mobile-responsive marketing / report / profile (workspace exempt)

Per plan §26, the workspace is desktop-only — that's defensible. But the
**landing, missions list, report, and profile** *must* render correctly on
phones, because that's where candidates will share their profile from. The
landing 3D scene in [Hero3D.tsx](apps/web/components/marketing/Hero3D.tsx)
will need a mobile fallback.

### P1-16. Resume / recover sessions after sandbox-reaper kill

The idle reaper destroys sandboxes after 30 min. Today this transitions the
session to `abandoned` and the user loses everything. A "Resume" path that
re-provisions a sandbox, re-applies the user's last diff, and continues the
timeline would convert "you got reaped, retry from scratch" (frustration) into
"continue where you left off" (forgiveness). This is the difference between
a tool and a teacher.

---

## P2 — Recommended but not blocking

Listed for completeness and roadmap visibility. None of these would prevent
the product from being usable today; all of them would *amplify* it.

- Public docs site / blog at `docs.openagentdojo.app` for SEO. The
  in-repo `docs/` content is already strong; surfacing it crawlably costs
  little.
- Mission-contribution flow for community-authored missions (explicitly
  deferred in plan §26, but worth a public roadmap entry so contributors
  don't waste effort).
- VS Code / Cursor extension that mirrors the supervision rubric against
  the user's *actual* agent — high-effort, but the most defensible long-term
  bet for the platform.
- SSO (SAML) / SCIM for organizations — *only* after P1-12 (team dashboard).
- Public per-mission leaderboard — fun, low cost, doubles as a calibration
  surface.
- Discord/Slack community link in the footer.
- Newsletter sign-up on the landing page (collect demand signal pre-launch).
- Pricing UI — gated on hitting the OQ-0003 threshold (≥1000 graded
  submissions).

---

## 4. What's already working (don't regress these)

These are the load-bearing strengths the gap list assumes will continue to
work. They are not "missing" — they are the parts the team should defend.

- **Deterministic grading** with a cached prompt judge and `RUBRIC_VERSION`
  bumping for re-judgement campaigns ([CONTEXT.md](CONTEXT.md) determinism
  rule 2).
- **Hard caps and floors** in the rubric — 0/15 on Agent Review for
  submit-within-15s-no-diff, hidden-test cap of 18 on Final Correctness —
  prevent the gameable shortcuts the rubric is designed to defeat.
- **Process-only score preview** during the session
  ([ScorePreview.tsx](apps/web/components/workspace/ScorePreview.tsx)) —
  the right call per OQ-0001.
- **Append-only event log** + WS replay UI; the Timeline component reads the
  same `supervision_events` rows the grader does.
- **Sandbox isolation posture** — rootless Docker, `--cap-drop=ALL`,
  `--network=none`, no host mounts, cgroups caps, seccomp.
- **The 7-dimension rubric is a single source of truth** —
  [`apps/api/app/grading/dimensions.py`](apps/api/app/grading/dimensions.py)
  is imported by the runner, the profile aggregator, and the schema
  validator. Drift is structurally prevented.
- **Mission self-tests** — every mission ships an `acceptance.yaml` and the
  CI gate is `pnpm validate:missions`. This is the calibration story
  most products of this shape don't have.
- **The redesigned marketing surface** — the dojo aesthetic + monospaced
  artifacts (Hero, HowItWorks, ScenarioCarousel, SampleReport, Footer) lead
  with the actual product instead of stock illustrations.

---

## 5. Things deliberately *not* listed as gaps

Per [IMPLEMENTATION_PLAN.md §26](IMPLEMENTATION_PLAN.md), the team has chosen
to defer these. Including them in this document would misrepresent intent:

- LLM grading on the hot path (intentionally forbidden — see ADR
  [0002-deterministic-agent.md](docs/adr/0002-deterministic-agent.md)).
- Real-time multiplayer / pair supervision.
- Mobile-optimized **workspace** (the read-only sub-surfaces are still
  expected to be mobile-responsive — see P1-15).
- Internationalization beyond English.
- Browser-based code execution / WASM sandbox.
- User-authored missions in MVP (P2 roadmap entry is fine).

These are real product choices, not oversights. The gap list above is calibrated
to not relitigate them.

---

## 6. Documentation / hygiene drift surfaced during this audit

Not features, but artifacts of writing being out-of-sync with code. Cheap to
fix; load-bearing for trust:

- IMPLEMENTATION_PLAN §11.1 lists `verification=20, diff_minimality=5`. The
  shipped rubric in
  [`dimensions.py`](apps/api/app/grading/dimensions.py) ships
  `verification=15, diff_minimality=10`. Source of truth must be one place.
- Mission YAMLs say `verification: 15, diff_minimality: 10` and so does
  [`Hero.tsx`](apps/web/components/marketing/Hero.tsx) — code and content
  agree; only the plan disagrees. Update the plan.
- README "Status: MVP complete" + "License: Internal MVP — not for
  redistribution" conflicts with the repo being public on GitHub.
- No `LICENSE` file at the repo root.
- No `CONTRIBUTING.md`. The mission-authoring path is in
  [docs/onboarding.md](docs/onboarding.md) but contributors will look at
  the root first.

---

## 7. Open product questions the team must answer

The priority ordering above assumes a specific answer to each of these. If
the answers shift, so does the ordering.

1. **Is the *primary* audience the practicing engineer (self-study) or the
   tech lead (team training)?** If self-study, P0-7 (identity) and P0-8
   (anti-cheating) soften; if team training, P1-12 jumps to P0.
2. **Is the public profile actually intended as a credential, or just a
   self-study artifact?** If credential, P0-7 + P0-8 + P0-11 + P1-7 are
   non-negotiable. If self-study only, restate the marketing copy to match.
3. **Is bring-your-own-repo on the multi-quarter roadmap?** If yes, the
   product can run on a small curated catalog forever. If no, P1-1 has to
   scale to dozens of missions.
4. **What is the retention story past the 10-mission catalog?** Without an
   answer, P1-1 is the bottleneck on every other ambition.
5. **Will the platform host a *live* LLM-driven agent (Cursor-like) or stay
   a hybrid-simulation forever?** A "supervise your real agent" mode is the
   single biggest moat available; it's also a different product. Decide
   before committing more content investment.

---

## 8. How to use this document

- Treat it as a prioritized to-do list, not a roadmap. The roadmap is
  [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md); this document is the
  delta between that plan and what the product actually needs.
- P0 items should land before the first public launch beyond the existing
  audience.
- P1 items should land before any serious sales motion to teams.
- Re-audit every quarter; the goal-alignment bar moves as the audience
  matures.

— audit performed against branch `codex/goal`, 2026-05-23.
