# OpenAgentDojo — P1 Implementation Design (items 1–6)

Continues [P0_DESIGN.md](P0_DESIGN.md) (P0-1 — P0-6) and
[P0_DESIGN_11_13.md](P0_DESIGN_11_13.md) (P0-11 — P0-13). Same template per
item: goal, architecture, data model, API contract, frontend surface,
scoring / telemetry interactions, edge cases, testing, rollout, open
decisions. The shared sections of P0_DESIGN.md (§0 cross-cutting decisions,
§A dependency graph, §B what stays the same) apply here too — this document
only adds new constraints.

This batch closes the gap between *passing the MVP launch bar* and *actually
serving the primary user well* — defined by [FEATURE_GAPS.md
§P1](FEATURE_GAPS.md#p1--necessary-for-full-goal-alignment). Items P1-7
through P1-16 are designed in a sibling document and are out of scope here.

---

## 0. Where this batch slots into the migration timeline

[P0_DESIGN.md](P0_DESIGN.md) and [P0_DESIGN_11_13.md](P0_DESIGN_11_13.md)
reserved migrations 0011 — 0020 on the design surface; the shipped tree at
`apps/api/alembic/versions/` is currently at **0024** after the OAuth +
session-mode + magic-link-next migrations landed. This batch claims:

| Migration | File | P1 item | Adds |
|---|---|---|---|
| 0025 | `0025_mission_tags_and_pack_metadata.py` | P1-1 | `missions.tags`, `missions.repo_pack_id`, `repo_packs` table |
| 0026 | `0026_recommendation_cache.py` | P1-2 | `user_recommendations` materialised cache |
| 0027 | `0027_session_notes.py` | P1-4 | `session_notes` table + `note.*` event types |
| 0028 | `0028_replay_artifact_index.py` | P1-6 | covering index on `supervision_events (session_id, occurred_at, id)` |
| 0029 | `0029_llm_cache.py` | §0.4 (P1-1, P1-2, P1-4) | `llm_cache` table for LLM-generated user-facing prose, keyed by `(domain, content_hash, prompt_version)` |

P1-3 (LSP) and P1-5 (side-by-side diff polish) require **no** schema
changes — P1-3 is a pure runtime/sandbox surface and P1-5 is a re-shaping
of data already produced by P0-2.

The new supervision-event types added by this batch — `note.edited`,
`note.viewed_during_prompt` — join the canonical catalogue documented in
[`docs/schemas/event.schema.json`](docs/schemas/event.schema.json) and at
[P0_DESIGN.md §0.3](P0_DESIGN.md). The recommendation engine (P1-2) and
the replay export (P1-6) emit **telemetry** events only — they do not
participate in `supervision_events` because they are not scored.

---

## 0.1 Determinism vs LLM-augmented prose — the split

The platform's load-bearing invariant is **determinism on the grading
path and on every signed artefact**. That invariant survives LLM use
*if and only if* the LLM never sits on a hot rendering path — every
LLM output is generated once, hashed against its inputs, persisted to
the `llm_cache` table (§0.4), and served from cache thereafter. This
is the same discipline the existing prompt judge under
[`apps/api/app/grading/prompt_judge.py`](apps/api/app/grading/prompt_judge.py)
already uses; §0.4 generalises it.

Concretely, this design splits each LLM-tempting surface into two
halves:

| Surface | Deterministic half (pure Python, hot path) | LLM-augmented half (cached, off hot path) |
|---|---|---|
| P1-2 next-mission engine | Ranking of missions (algorithm in §P1-2) | Diagnosis prose + per-mission `why` strings |
| P1-4 scratchpad | Storage + event coalescing | Post-mortem coaching reflection (§P1-4 "Coaching reflection") |
| P0-2 critical-moment prose | Heuristic that picks the moment + the affected event id | `explanation` + `what_to_do_instead` prose polish |
| P1-1 catalog expansion | Mission validator + repo-pack pinning | Mission-authoring draft tool (contributor-side, not runtime) |

The following surfaces remain **pure deterministic, no LLM ever**:

- The grading rubric and dimension scorers
  ([`apps/api/app/grading/dimensions.py`](apps/api/app/grading/dimensions.py),
  [`apps/api/app/grading/score.py`](apps/api/app/grading/score.py)) —
  ADR 0002 invariant.
- The recommendation **ranking** (P1-2) — only the prose is LLM-polished;
  the ordered list of mission ids is a pure function.
- The verification envelope and its signature (P0-11) — the envelope is
  built from rows the LLM cache may have produced, but the bytes hashed
  into the signature are the cached bytes, never a live LLM call.
- The replay artifact (P1-6) — same as above. Same submission with the
  same cache state always produces the same signed bytes. Cache
  invalidation (`prompt_version` bump) is an operator action equivalent
  to bumping `RUBRIC_VERSION`: it is expected to change downstream
  signatures, and the existing re-judgement campaign machinery handles
  it.

So the rule is: **the LLM is allowed to write prose; it is never
allowed to score, rank, or sign.** Everything below honours that.

---

## 0.2 New telemetry events

The recommendation, scratchpad, LSP, and replay items each surface a thin
slice of telemetry. The full new event list:

| Event | Surface | Payload (sketch) |
|---|---|---|
| `recommendation_shown` | profile / catalog / report | `{kind, weakest_dim?, mission_ids, signed_in: bool}` |
| `recommendation_clicked` | same | `{position: 0..N, mission_id}` |
| `scratchpad_opened` | workspace | `{session_id}` |
| `scratchpad_edit_persisted` | workspace | `{session_id, bytes, debounced_ms}` |
| `lsp_session_started` | workspace | `{language, cold_start_ms}` |
| `lsp_completion_accepted` | workspace | `{language}` |
| `lsp_error` | workspace | `{language, error_class}` |
| `replay_export_requested` | report / account | `{submission_id, kind: 'json'\|'zip'}` |
| `replay_export_succeeded` | report / account | `{submission_id, bytes}` |
| `replay_export_failed` | report / account | `{submission_id, error_class}` |
| `llm_cache_hit` | any LLM-augmented surface | `{domain, prompt_version}` |
| `llm_generation_succeeded` | any LLM-augmented surface | `{domain, model_id, input_tokens, output_tokens, latency_ms}` |
| `llm_generation_failed` | any LLM-augmented surface | `{domain, model_id, error_class}` |

`scratchpad_edit_persisted` and `lsp_completion_accepted` are the
load-bearing signals for product-tuning P1-4 and P1-3 respectively;
without them we cannot tell whether the features land. `llm_cache_hit`
vs `llm_generation_succeeded` (their ratio) is the load-bearing
operational metric — if cache-hit drops below ~80 % in steady state,
either the input key is too granular or the prompt_version was bumped
without warning.

---

## 0.3 What this batch does *not* try to do

To keep scope honest, three temptations are explicitly out:

1. **No LLM on the grading hot path, the ranking layer, or any signed
   artefact's byte stream.** LLM-polished *prose* is allowed via the
   §0.4 cache discipline; LLM-driven *decisions* (score, ranking,
   signature) are forbidden by ADR 0002 and §0.1 above.
2. **No scoring impact for scratchpad notes.** P1-4 ships the artefact and
   the event stream; *measuring* "did the user think before prompting"
   waits for telemetry data showing the habit actually correlates with
   final score. Premature scoring would punish a habit we have not yet
   proven matters.
3. **No "intelligent" repo-pack synthesis.** P1-1 ships one new
   hand-authored pack, not a pack generator. The "real repositories"
   claim is upheld by quality, not quantity of synthesis. The LLM-assisted
   *authoring* scaffold (§P1-1 below) drafts a mission *for a human
   reviewer*; it does not ship missions into production unattended.

---

## 0.4 LLM use policy (Bedrock client + cache table)

The platform standardises on the existing Civitas/Anthropic pattern
already documented at [`keys.md`](keys.md): an Anthropic SDK client
that auto-detects `ANTHROPIC_PROVIDER=bedrock` plus
`AWS_BEARER_TOKEN_BEDROCK` + `AWS_REGION` and constructs
`AsyncAnthropicBedrock`, falling back to direct `AsyncAnthropic`
otherwise. The logical-id → Bedrock inference-profile mapping
(`claude-opus-4-7`, `claude-sonnet-4-6`, `claude-haiku-4-5`) is the
same set used by the rest of the org.

### 0.4.1 Client wiring

```
[ apps/api/app/llm/ ]                                  NEW MODULE
    client.py
        build_anthropic_client() -> AsyncAnthropic|AsyncAnthropicBedrock
        resolve_model_id(logical_id: str) -> str
        # mirrors civitas_core.llm.anthropic_client behaviour but lives
        # inside this repo so the dependency stays minimal.
    cache.py
        get_or_generate(domain, content_hash, prompt_version,
                        model_id, generator) -> str
        # The single chokepoint every LLM-augmented surface uses.
        # Returns cached bytes if present; otherwise invokes generator
        # (an async lambda that hits the SDK), writes to llm_cache,
        # emits llm_cache_hit / llm_generation_succeeded telemetry,
        # returns the bytes.
    domains.py
        Literal type listing the allowed `domain` values; one constant
        per LLM use site below. New domains require an ADR.
    prompts/
        recommendation_diagnosis.md     ← Jinja2 prompt template
        recommendation_why.md
        scratchpad_coaching.md
        critical_moment_polish.md
        mission_authoring_draft.md
    PROMPT_VERSION = 1                  ← bump on prompt-template edits
                                          (invalidates cache rows)
```

`build_anthropic_client()` reads the same env vars `keys.md` documents:

```python
ANTHROPIC_PROVIDER          # 'bedrock' | <unset>
AWS_BEARER_TOKEN_BEDROCK    # opaque Bedrock bearer token
AWS_REGION                  # e.g. 'us-east-2'
# Optional override; resolves logical → inference-profile id:
# 'claude-haiku-4-5'  -> 'us.anthropic.claude-haiku-4-5-...'
# 'claude-sonnet-4-6' -> 'us.anthropic.claude-sonnet-4-6-...'
# 'claude-opus-4-7'   -> 'us.anthropic.claude-opus-4-7-...'
```

Direct-Anthropic fallback uses `ANTHROPIC_API_KEY` if set; this is the
local-dev path. Production runs Bedrock.

### 0.4.2 The `llm_cache` table (migration 0029)

```sql
CREATE TABLE llm_cache (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    domain          TEXT NOT NULL,
        -- closed vocabulary; see apps/api/app/llm/domains.py
    content_hash    TEXT NOT NULL,
        -- SHA-256 hex of the canonicalised inputs (see §0.4.3)
    prompt_version  INTEGER NOT NULL,
        -- bumps invalidate every row under this domain; mirrors the
        -- RUBRIC_VERSION discipline.
    model_id        TEXT NOT NULL,
        -- logical id at write time; 'claude-haiku-4-5' etc. Recorded
        -- so re-judgement campaigns know what was used.
    output          TEXT NOT NULL,
        -- the generated bytes, verbatim. Treated as opaque content;
        -- downstream signatures hash THESE bytes, not a regeneration.
    input_tokens    INTEGER NULL,
    output_tokens   INTEGER NULL,
    generated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (domain, content_hash, prompt_version)
);

CREATE INDEX idx_llm_cache_lookup
    ON llm_cache (domain, content_hash, prompt_version);
```

`UNIQUE (domain, content_hash, prompt_version)` guarantees one canonical
output per (inputs × prompt template) pair. The same inputs from any
caller return the same bytes — that is what makes downstream
signatures (P0-11 verify envelope, P1-6 replay artefact) survive LLM
use without losing their determinism property.

### 0.4.3 Canonical input hashing

Every domain has a Pydantic input model whose serialised form (sorted
keys, no whitespace) is fed into SHA-256 to produce `content_hash`.
Input models are stable across releases — adding a field is an
ADR-level change because it shifts every cache key under that domain.

Example for `recommendation_diagnosis`:

```python
class RecommendationDiagnosisInputs(BaseModel):
    weakest_dim: str | None          # e.g. 'agent_review'
    weakest_dim_avg: float | None    # e.g. 8.4 — rounded to 1dp before hash
    weakest_dim_attempts: int | None
    recommended_mission_ids: tuple[str, ...]   # ordered, deterministic
    rubric_version: str              # 'v1' — locks against re-balance drift
```

`weakest_dim_avg` is rounded to 1 decimal place before hashing so a
trivial floating-point delta doesn't bust the cache. This is
documented per-domain in `apps/api/app/llm/domains.py`.

### 0.4.4 Model-selection rules

| Use site | Model | Reason |
|---|---|---|
| `recommendation_diagnosis` (P1-2) | `claude-haiku-4-5` | Short coaching prose; ~50–120 tokens; high cache-hit rate; needs to be cheap. |
| `recommendation_why` (P1-2) | `claude-haiku-4-5` | Same shape; per-mission micro-prose. |
| `critical_moment_polish` (P0-2 augmentation) | `claude-haiku-4-5` | Templated input, polish output; fast. |
| `scratchpad_coaching` (P1-4 post-mortem reflection) | `claude-sonnet-4-6` | Needs to reason over scratchpad text *and* event stream; nuanced output. |
| `mission_authoring_draft` (P1-1 contributor tool) | `claude-opus-4-7` | Highest quality; called rarely (per-mission, by a human author); cost irrelevant. |

The choice is recorded in the cache row; future cache invalidations
re-emit using whatever model is configured at the time, so model
upgrades land naturally on the next `prompt_version` bump.

### 0.4.5 Fallback behaviour (graceful degradation)

Every LLM-augmented surface ships a deterministic fallback string,
used when:

- `llm_cache` lookup misses AND
- The live LLM call fails (Bedrock 5xx, region outage, token revoked,
  or `ANTHROPIC_PROVIDER` unset in some dev environments).

The fallback is the templated string the prior design assumed
exclusively (e.g., "You skip the diff most of the time — try these
three"). It is good enough that the product still works without the
LLM; the LLM polish is *quality lift*, not *correctness*. The
fallback is NOT written to `llm_cache` (so a future retry can succeed
and populate the cache), but IS returned to the user inline. The
fallback path emits `llm_generation_failed` telemetry so an operator
sees the breakage immediately.

### 0.4.6 Privacy posture for LLM inputs

- **Scratchpad text** (P1-4) goes only to the `scratchpad_coaching`
  domain. The privacy policy (P0_DESIGN P0-5) is updated to disclose
  this clearly, including that the prompt text is sent to Anthropic
  via Bedrock.
- **Prompt text** from supervision events is NOT sent to any LLM in
  this batch. P0-2's `critical_moment_polish` receives a structured
  description (event kind + file + line range), never the user's raw
  prompt body.
- **User identifiers** (handle, email, display_name) are NEVER part
  of an LLM prompt. Cache keys hash mission ids and dimensions, never
  user PII.
- **Bedrock data-handling**: AWS Bedrock does not train on customer
  data by default. The privacy page lists Bedrock as a sub-processor
  with that posture explicit.

### 0.4.7 Rate limits and budget guards

- Per-request hard cap: every LLM use site sets `max_tokens` to twice
  its expected output size (e.g. recommendation diagnosis caps at 256
  tokens). A model that runs away cannot blow the budget.
- Per-user soft cap: scratchpad coaching runs at most twice per
  submission (post-mortem first view + one explicit refresh). After
  that, the cached output is returned.
- Per-day soft cap: `llm_generation_succeeded` events are aggregated
  by a daily Prometheus rule; alerts fire if daily token spend
  exceeds 2× the 7-day moving average.
- Bedrock token rotation: documented at `keys.md` and the operations
  runbook; no code change needed when the bearer is rotated because
  the SDK picks it up from env.

---

## P1-1. Expand the mission catalog (volume + diversity)

### Goal

A returning user who finished the existing 11 missions in an evening has a
visible, dated path to **more training value** without the team having to
pre-write infinite content. Concretely:

1. **A third repo pack** that exercises a different stack from the
   existing two ([`fullstack-auth-demo`](missions/_shared/repos/fullstack-auth-demo)
   TS/Node + [`data-api-demo`](missions/_shared/repos/data-api-demo) Py/FastAPI).
2. **Three new missions on that pack** (taking the catalogue from 10
   standard + 1 tutorial → 13 standard + 1 tutorial).
3. **A public roadmap surface** showing dated placeholders for the next
   six missions so a returning user sees what is coming, not just what
   has shipped.
4. **A mission-authoring template + CLI** so new packs cost less than
   they did this round — paying down the per-mission marginal cost.

The recommended third pack is a **Go microservice**
(`go-orders-service`) because:

- Different runtime family from both shipped packs (Node + Python).
- Strict typing makes "patch looks right, isn't" bugs more visually
  honest — agents misuse `errors.Is` vs `==`, drop `context.Context`
  propagation, or smuggle goroutine leaks past a casual diff review.
- Goroutines and channels are a rich new failure-mode surface (deadlocks
  on unbuffered channel close, leaked goroutines on early return,
  `select`-default starvation) that the existing packs cannot host.
- Existing rootless-Docker base image already supports arbitrary
  binaries; only the test-runner harness changes (`go test ./...` →
  same JSON output schema as the existing TS/Py runners).

Alternative considered: a frontend-only React SPA pack
(`react-shop-demo`). Equally valid, but it would re-tread bug classes
the fullstack pack already exercises (component state desync,
accessibility regression). Defer to a future expansion; ship Go first.

### Architecture

```
[ missions/_shared/repos/go-orders-service/ ]      NEW REPO PACK
    cmd/orders/main.go            entrypoint
    internal/handlers/            HTTP layer (chi router)
    internal/store/               in-process repo with a sqlite backend
    internal/queue/               goroutine-driven worker pool
    go.mod / go.sum               pinned Go 1.22.x
    testdata/
    Makefile                      `make test` + `make vet` + `make race`
    .dockerignore + Dockerfile    rootless-friendly multi-stage build

[ missions/_shared/docker/go-orders.Dockerfile ]   image override
    FROM golang:1.22-bookworm + ripgrep + git
    HEALTHCHECK reports "go test ./..." compiles in < 5 s cold

[ missions/11-goroutine-leak/ ]                     NEW MISSION
[ missions/12-context-cancel-dropped/ ]             NEW MISSION
[ missions/13-error-shadowed-by-wrap/ ]             NEW MISSION
    each shipped with the full per-mission file set documented in
    missions/README.md

[ apps/api/app/missions/manifest.py ]
    + repo_pack_id: str           — already an implicit field in the
                                    mission.yaml; promoted to a typed
                                    column for query / filter use
    + tags: list[str]             — failure-mode + skill + language tags

[ scripts/mission-template/ ]                       NEW AUTHORING SCAFFOLD
    init.py                       interactive prompt
    template/                     mission.yaml + agent_patch.diff +
                                  forbidden_changes.yaml + acceptance.yaml
                                  + hidden_tests/runner.sh + prompts/
    README.md                     mission-author workflow

[ apps/web/app/(marketing)/roadmap/page.tsx ]       NEW
    static MDX listing shipped missions, in-flight (with a github issue
    link), and dated placeholders. Re-rendered at build; auto-pulls
    mission count from missions/ at build-time.
```

### Data model (migration 0025)

```sql
CREATE TABLE repo_packs (
    id              TEXT PRIMARY KEY,        -- e.g. 'go-orders-service'
    title           TEXT NOT NULL,
    language        TEXT NOT NULL,           -- 'typescript','python','go'
    stack_summary   TEXT NOT NULL,           -- shown on the catalog filter
    repo_sha        TEXT NOT NULL,           -- pin against drift; CI gate
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE missions
    ADD COLUMN repo_pack_id TEXT NULL REFERENCES repo_packs(id),
    ADD COLUMN tags         TEXT[] NOT NULL DEFAULT '{}';

-- Backfill from the existing mission.yaml `repo_pack` field.
UPDATE missions m
SET    repo_pack_id = m.repo_pack
WHERE  m.repo_pack IS NOT NULL;

-- Lift the existing `repo_pack` string column to NOT NULL after
-- backfill; both names co-exist for one release cycle then `repo_pack`
-- is dropped in migration 0029.
ALTER TABLE missions ALTER COLUMN repo_pack_id SET NOT NULL;

CREATE INDEX idx_missions_repo_pack ON missions (repo_pack_id);
CREATE INDEX idx_missions_tags      ON missions USING GIN (tags);
```

`tags` is a free-form array; the validator caps it at 8 tags per
mission and enforces a known-vocabulary subset (see `tags` section
below).

### Mission manifest schema delta

[`docs/schemas/mission.schema.json`](docs/schemas/mission.schema.json)
grows:

```json
{
  "tags": {
    "type": "array",
    "maxItems": 8,
    "uniqueItems": true,
    "items": {
      "enum": [
        // failure-mode tags (one required)
        "checks_presence_not_expiration",
        "overfitted_visible_test",
        "wrong_layer_committed",
        "missing_regression_test",
        "race_condition",
        "context_dropped",
        "error_wrapped_swallowed",
        "dependency_misuse",
        "security_check_removed",
        "typecheck_ignored",
        "api_contract_drift",
        "excessive_rewrite",
        "goroutine_leak",
        // skill tags (optional)
        "skill:concurrency",
        "skill:typing",
        "skill:auth",
        "skill:http",
        "skill:sql",
        "skill:cli",
        // language tags (auto-inferred from repo_pack, but allowed)
        "lang:typescript", "lang:python", "lang:go"
      ]
    }
  }
}
```

`pnpm validate:missions` extends to:

- Every mission MUST carry at least one failure-mode tag.
- Every mission's failure-mode tag MUST match the `failure_mode` field
  on the manifest (no drift).
- Tag vocabulary is closed (above list). New tags require an ADR.

### The three new Go missions

Designed against the new pack. Each ships the standard mission file set:

| # | Folder | Failure mode | Hidden-test class | Difficulty |
|---|---|---|---|---|
| 11 | `11-goroutine-leak` | `goroutine_leak` | `runtime.NumGoroutine` delta + race detector | intermediate |
| 12 | `12-context-cancel-dropped` | `context_dropped` | request-cancel propagation + handler timeout | intermediate |
| 13 | `13-error-shadowed-by-wrap` | `error_wrapped_swallowed` | `errors.Is` and structured error code | beginner |

Each mission's `agent_patch.diff` mutates the right files but the wrong
*line* — same dojo signature as the existing missions. Each carries a
`forbidden_changes.yaml` that flags "the agent rewrote the entire
package" / "the agent introduced a new dependency that wasn't asked
for" (analogous to mission 06's anti-patterns).

### Mission-authoring scaffold (LLM-assisted, contributor-side)

`scripts/mission-template/` ships a small Python tool. The skeleton
generator is pure-template; the *draft generator* is opt-in and
LLM-backed (`--with-llm-draft`), targeting the
`mission_authoring_draft` domain in §0.4.

```bash
$ python scripts/mission-template/init.py --with-llm-draft
mission id (kebab-case)? request-deadline-stripped
repo pack? [fullstack-auth-demo|data-api-demo|go-orders-service] go-orders-service
failure mode? context_dropped
estimated_minutes (10-30)? 15
brief? "agent strips context.WithDeadline when refactoring the handler"

→ created missions/14-request-deadline-stripped/
   ├── mission.yaml              ← templated skeleton, ready to edit
   ├── README.md                 ← templated
   ├── hidden_tests/runner.sh    ← templated
   ├── prompts/                  ← templated scaffolds
   ├── acceptance.yaml           ← skeleton with placeholder bands
   └── _draft/                   ← LLM drafts, NOT merged into final files
       ├── agent_patch.draft.diff
       ├── ideal_solution.draft.diff
       ├── ideal_solution.draft.md
       ├── prompts/response.draft.md
       └── hidden_tests/auth.hidden.draft.test.go

   The _draft/ directory is excluded from `pnpm validate:missions` and
   from the published mission bundle. The author MUST hand-review every
   file, port it into the canonical location, run the visible tests +
   hidden tests on initial_commit + agent_patch (must fail at least one
   hidden test), and on initial_commit + ideal_solution (must pass all).
```

**Constraint.** The LLM-drafted files land under `_draft/` only. A
mission cannot ship until the author has hand-promoted every draft
into the canonical location (the validator checks for the presence of
`_draft/` and fails CI if it still exists). This forces a human in
the loop on every piece of grader-visible content — which is the
non-negotiable rule for a tool whose purpose is *teaching the eye that
catches agent failures*. We do not let an agent author the agent's
own failures unattended.

The draft generator uses `claude-opus-4-7` (per §0.4.4). The prompt
template at `apps/api/app/llm/prompts/mission_authoring_draft.md`
receives:

- The brief + failure_mode + repo_pack manifest summary,
- A few-shot of two existing missions in the same pack as exemplars,
- The mission schema's required fields,
- A hard requirement that `agent_patch.diff` mutate the *right file* on
  the *wrong line* (the dojo signature) and `ideal_solution.diff` apply
  cleanly on `initial_commit`.

This is contributor tooling — no API surface, no production image,
no runtime path. The Bedrock client is the same one §0.4 wires for
runtime use, but this caller passes through `apps/api/app/llm/cli.py`
which writes drafts to the local filesystem and never to `llm_cache`
(per-author runs would pollute the table). Telemetry from this path
fires `llm_generation_succeeded` with `domain="mission_authoring_draft"`
so cost is tracked.

### API surface

The catalog endpoint
[`GET /api/v1/missions`](apps/api/app/missions/router.py) gains optional
query filters:

```
GET /api/v1/missions?tags=race_condition&repo_pack=go-orders-service
GET /api/v1/missions?language=go
GET /api/v1/missions?include=upcoming     ← new
```

`include=upcoming` includes dated placeholders (status `coming_soon`)
read from a static YAML at
`apps/api/app/missions/roadmap.yaml`. The placeholders ship a title,
target date, and a one-line teaser — no payload, no manifest, no
agent_patch.

`MissionSummary` response shape grows:

```typescript
type MissionSummary = {
  // ... existing fields
  repo_pack_id: string;
  language: 'typescript' | 'python' | 'go';
  tags: string[];
  status: 'shipped' | 'coming_soon';
  target_release_date?: string;       // present iff status === 'coming_soon'
};
```

### Frontend surface

[`MissionGrid.tsx`](apps/web/components/catalog/MissionGrid.tsx) gains:

- A **language chip** per card (`// go`, `// ts`, `// py`) on the
  bottom-right, matching the existing dojo aesthetic.
- A **filter strip** above the grid: `All · TypeScript · Python · Go`
  plus a `// failure mode ▾` dropdown driven by the tag vocabulary.
- An **"Up next"** row at the bottom rendering `coming_soon` cards in
  a slightly muted style with the dated chip and a "watch repo" link.

[`apps/web/app/(marketing)/roadmap/page.tsx`](apps/web/app/(marketing)/roadmap/page.tsx)
is a public, signed-out-friendly page mirroring the same `coming_soon`
list with one paragraph of pedagogical framing. Linked from the
marketing footer.

### Scoring interactions

None. Mission tags / repo packs are content metadata; they do not
influence the rubric. The Go test-runner produces the same JSON envelope
the existing TS/Py runners produce (test name, status,
duration, file). The grader is repo-pack-agnostic by construction
because it reads the runner's JSON, not the language-specific output.

The hidden-test runner for Go is at
`missions/_shared/docker/runners/go-runner.sh`:

```bash
#!/bin/bash
# Runs `go test -run "$TEST" -json ./...` and re-emits the events into the
# same {name, status, duration_ms, file} shape the grader expects.
set -euo pipefail
exec go test -run "$TEST_PATTERN" -json ./... | \
  python /usr/local/bin/go-test-events-to-grader.py
```

The bridge script lives alongside the existing TS/Py bridges; same
contract.

### Edge cases

- **Mission with no failure-mode tag** — validator fails the CI gate
  `pnpm validate:missions`. Cannot merge.
- **Coming-soon date in the past** — validator fails. Roadmap stays
  honest.
- **Repo pack drift** — `repo_packs.repo_sha` is the SHA of the
  pack's `git rev-parse HEAD` at the time of seeding. The seed script
  re-asserts it on every deploy; mismatch → deploy halts.
- **Go race detector flakes** — race-detector tests run with a `-count=3`
  wrapper and only fail the mission if all three iterations agree.
  Documented in the mission's README as expected behaviour.
- **Returning user with all 13 missions completed** — catalog still
  shows the `coming_soon` row, so the experience is "what is next" not
  "you finished everything."

### Testing

- Pytest `test_mission_tag_validator.py` — every shipped mission carries
  a known-vocabulary failure-mode tag and the tag matches `failure_mode`.
- Pytest `test_repo_pack_sha_pinned.py` — `repo_packs.repo_sha` matches
  the on-disk pack SHA in CI; deploys fail on mismatch.
- Pytest `test_go_runner_envelope.py` — go-test-events-to-grader bridge
  produces the same JSON shape on a fixture `go test -json` output.
- Mission self-test (each new Go mission) — `acceptance.yaml` envelope
  asserts the calibrated score band, same as existing missions.
- Vitest `mission-grid-filter.test.tsx` — filtering by language /
  failure mode renders the expected subset, including the coming-soon
  row.

### Rollout

Three PRs:

1. **PR1** — migration 0025, manifest schema delta, repo_packs seed,
   `MissionSummary` API change, mission-tag validator. Zero
   user-visible change yet.
2. **PR2** — `go-orders-service` pack + Go runner bridge + Dockerfile.
   No new missions yet; the pack ships behind an internal
   feature flag (`features.go_pack_available = false`).
3. **PR3** — the three new Go missions + the catalog filter + roadmap
   page. Flag flipped on.

### Open decisions

- **Should the pack include a real database, or use in-process sqlite?**
  Recommendation: in-process sqlite. A real DB image multiplies sandbox
  startup cost (~3-5 s) for marginal pedagogical value at this volume.
- **Roadmap dating cadence.** Recommendation: monthly. Quarterly
  reads as vapour to a returning user; weekly is dishonest given
  authoring time.

---

## P1-2. Adaptive next-mission engine

### Goal

The product produces a **personalised, pedagogically defensible** "what to
work on next" recommendation for every signed-in user, surfaced on three
surfaces:

1. The **profile** page (`/profile/{handle}` when the viewer is the
   owner). One-paragraph diagnosis ("your weakest dimension is
   `agent_review`") followed by three ranked missions.
2. The **catalog** (`/missions`). A `// recommended` chip on the single
   highest-ranked mission for the signed-in user. Subtle, not pushy.
3. The **report** page bottom CTA. "Next mission →" is already there
   (P0-3) but currently points at the score_report's
   `recommended_mission_ids[0]`; this item replaces that field's
   ad-hoc-per-dimension list with a *globally* ranked recommendation
   for the user.

The recommendation splits cleanly into two halves per §0.1:

- **Ranking — pure, deterministic, no LLM.** Same user history →
  same ordered `recommended_mission_ids`, byte-identical across
  replays. This is what the signed score_report's
  `feedback_narrative[].recommended_mission_ids` field hashes against
  (P0-11 verify envelope).
- **Prose — LLM-polished via §0.4 cache.** The `diagnosis` and
  per-mission `why` strings are generated by `claude-haiku-4-5`,
  keyed by `(weakest_dim, weakest_dim_avg_1dp, recommended_mission_ids,
  rubric_version)` so the same ranking always produces the same prose.
  Templated fallback covers Bedrock outages.

### Algorithm (deterministic ranking — no LLM)

A pure function `recommend(user_history, mission_catalogue) →
[Recommendation]`:

```
1. Compute per-dimension averages across the user's best-per-mission
   submissions (already done by the profile aggregator for P0-3).

2. Identify weakest_dim = argmin(dim_avg) where the user has ≥ 1
   graded submission. Tie-break by the canonical RUBRIC_DIMENSIONS
   order so identical inputs produce identical outputs.

3. For each not-yet-passed mission (best_score < pass_threshold OR
   never attempted):
     score = (
         dim_alignment      # 1.0 if mission.expected_weak_dim == weakest_dim
                            # 0.5 if mission has weak_dim in its tags
                            # 0.0 otherwise
       + difficulty_match   # 1.0 if difficulty == "your current band"
                            # 0.5 if one band off
                            # 0.0 otherwise
       + freshness          # 1.0 if mission shipped after user's last
                            # graded submission, else 0
       + novelty_bonus      # 0.5 if user has never attempted this
                            # mission, else 0
     )

4. Sort by score desc; tie-break by mission_id asc (deterministic).
   Take the top 3.

5. If the user has zero graded submissions: skip the diagnosis text;
   return the top 3 of the global "introductory ladder" (missions
   01, 02, 03 in current order).
```

`mission.expected_weak_dim` is a new manifest field — a single
dimension the mission is *primarily designed to exercise*. Already
implicit in the calibration envelopes; lifted to an explicit field by
this design.

The algorithm runs on the API server per request; results are cached
per user in `user_recommendations` (see Data model) with TTL =
`max(last_submission_at, last_mission_publish_at) + 1 hour`. Hot path
hits the cache; cache misses recompute.

### Architecture

```
[ apps/api/app/missions/manifest.py ]
    + expected_weak_dim: Literal[<7 dimension names>] | None
        — required for non-tutorial missions in the schema delta below

[ apps/api/app/recommendations/ ]                   NEW MODULE
    engine.py         pure recommend(...) function — RANKING ONLY,
                      no LLM, no I/O beyond reading already-aggregated
                      score_report rows
    cache.py          user_recommendations read/write helpers
    prose.py          generate_diagnosis(...) and generate_why(...) —
                      both route through apps/api/app/llm/cache.get_or_generate
                      with prompt templates from
                      apps/api/app/llm/prompts/recommendation_*.md.
                      Cache-keyed; templated fallback on Bedrock errors.
    router.py         GET /api/v1/me/recommendations
    schemas.py        Pydantic surface

[ apps/api/app/profiles/router.py ]
    + GET /me  ← extended with `recommendation` field for signed-in viewer

[ apps/api/app/reports/router.py ]
    feedback_narrative[].recommended_mission_ids stays for backward
    compatibility but is now populated FROM the recommendation engine,
    not from per-dimension static lists.

[ apps/web/app/(app)/profile/[handle]/page.tsx ]
    + RecommendationStrip component when viewer == owner

[ apps/web/app/(app)/missions/page.tsx ]
    + recommended chip on the single highest-ranked mission card

[ apps/web/components/report/ReportFooter.tsx ]
    "Next mission →" reads from /me/recommendations rather than from
    the embedded score_report.feedback_narrative
```

### Data model (migration 0026)

```sql
CREATE TABLE user_recommendations (
    user_id          UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    weakest_dim      TEXT NULL,                 -- nullable for cold-start users
    recommended_ids  TEXT[] NOT NULL,           -- ordered, length 3
    computed_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    invalidated_at   TIMESTAMPTZ NULL           -- explicit invalidation
);

ALTER TABLE missions
    ADD COLUMN expected_weak_dim TEXT NULL
        CHECK (expected_weak_dim IS NULL OR expected_weak_dim IN (
            'final_correctness','verification','agent_review',
            'prompt_quality','context_selection','safety','diff_minimality'
        ));

-- Backfill from the calibration envelopes. The envelope's lowest-band
-- dimension is the mission's designed weak point.
UPDATE missions m
SET    expected_weak_dim = subq.dim
FROM   (SELECT … FROM missions_calibration ...) AS subq
WHERE  m.id = subq.mission_id;

-- After backfill, lift NOT NULL on standard missions; tutorials remain
-- nullable.
ALTER TABLE missions
    ADD CONSTRAINT missions_kind_weak_dim_required
        CHECK (kind = 'tutorial' OR expected_weak_dim IS NOT NULL);
```

The cache is invalidated on three events: new graded submission for the
user; new mission published; ADR-driven rubric re-balance (operational
flush via a `scripts/invalidate_recommendations.py` one-shot).

### API surface

```
GET /api/v1/me/recommendations
  Auth: required.
  Response:
    {
      weakest_dim: 'agent_review' | 'verification' | ... | null,
      diagnosis: "You skip the diff most of the time — try these three.",
      recommendations: [
        { mission_id, title, language, difficulty,
          why: "exercises agent_review through a misnamed function",
          your_best_score: null | number,
          your_attempts: number
        },
        ...   // total 3
      ],
      computed_at: iso8601,
      cache_hit: bool
    }
  The `diagnosis` and per-mission `why` strings are generated via the
  §0.4 LLM cache (model: claude-haiku-4-5, domain:
  recommendation_diagnosis / recommendation_why). Cache key includes
  (weakest_dim, weakest_dim_avg rounded to 1dp, the ordered
  recommended_mission_ids tuple, rubric_version). The deterministic
  fallback strings (templated per dimension / failure-mode pair) are
  returned when Bedrock is unavailable; these fallbacks live in
  apps/api/app/recommendations/fallback_copy.py and are unit-tested.

  The endpoint emits one of:
    - llm_cache_hit             (warm path, ~99% in steady state)
    - llm_generation_succeeded  (cold path; rare)
    - llm_generation_failed     (Bedrock 5xx, fallback served)
  per call.
```

`GET /me` is extended to include the top recommendation inline so the
header `[ Resume →]` affordance does not need a separate roundtrip on
sign-in. The inline shape uses the cached prose; cold-cache first-render
falls through to the fallback copy to avoid a sign-in-time Bedrock
round-trip blocking the header.

### Frontend surface

#### Profile recommendation strip

```
┌────────────────────────────────────────────────────────────────┐
│ // your next step                                              │
│                                                                │
│ Your weakest dimension is Agent Review (avg 8/15 across 4      │
│ missions). Try these three in order — each forces a habit      │
│ you're skipping.                                               │
│                                                                │
│ ┌──────────────────────────┐  ┌──────────────────────────┐    │
│ │ 02 · Agent picked the    │  │ 06 · Excessive rewrite   │    │
│ │      wrong file          │  │      under the guise of  │    │
│ │ // exercises agent_      │  │      a fix               │    │
│ │    review through a      │  │ // exercises agent_      │    │
│ │    misnamed function     │  │    review through diff   │    │
│ │ → Start                  │  │ → Start                  │    │
│ └──────────────────────────┘  └──────────────────────────┘    │
│                                                                │
│ ┌──────────────────────────┐                                  │
│ │ 09 · API contract drift  │                                  │
│ │ // exercises agent_      │                                  │
│ │    review + verification │                                  │
│ │ → Start                  │                                  │
│ └──────────────────────────┘                                  │
└────────────────────────────────────────────────────────────────┘
```

For cold-start users (no graded submissions): "Start the ladder" with
missions 01, 02, 03 and the `// orientation` chip.

#### Catalog `// recommended` chip

Single mission card (the top recommendation) gets:

```
┌──────────────────────────────┐
│ // recommended               │  ← muted accent colour, top-left chip
│ 06 · Excessive rewrite       │
│ ...                          │
└──────────────────────────────┘
```

Only ONE card carries the chip — multiple chips dilute the signal.

#### Report bottom CTA

```
[ ← Back to missions ]  [ ↻ Retry this mission ]  [ Next: 06 → ]
```

"Next" reads the top recommendation rather than the score_report's
embedded list, so the recommendation is always live (it would otherwise
drift as the user's history changes after the submission was graded).

### Scoring interactions

Strict separation: the recommendation engine reads `score_report` and
`submissions`, but does not write to either. The score itself is
unchanged by this feature.

`feedback_narrative[].recommended_mission_ids` continues to be persisted
for backward compatibility with the existing schema and the verify
artefact (P0-11). At write time the engine populates it with the
deterministic ranking output (never the LLM-polished prose, which is
view-time only); at read time the FE prefers the live
`/me/recommendations` over the embedded field when the viewer is the
owner. Because the persisted field is the pure ranking, the verify
envelope's signature is unaffected by LLM polish: rotating the prose
prompt template (`prompt_version` bump) does not invalidate any
existing signed report.

### Edge cases

- **User has only tutorial submissions.** Treated as cold-start; the
  introductory ladder is shown.
- **User has graded every shipped mission.** `recommendations` returns
  the *one* mission with the largest score gap to ideal (recommend
  retry), plus two `coming_soon` placeholders with their dates. The FE
  renders the placeholders distinct from active cards.
- **Mission catalogue empty (test env).** Engine returns an empty list;
  FE renders the cold-start ladder copy with a "no missions yet" note.
- **`expected_weak_dim = safety` but user's weakest dim is also safety
  AND they've already passed every safety-tagged mission.** Algorithm
  falls back to second-weakest dim. Deterministic by the same
  argmin/tie-break rule.
- **User's best-per-mission radar is uniformly excellent (no
  weakest_dim).** `weakest_dim = null`; the engine recommends the
  newest unfinished mission. Diagnosis copy: "You're solid across all
  dimensions — try the freshest missions to keep the edge sharp."
- **Cache stale across a rubric re-balance.** Operational flush via a
  one-shot script; the engine has no automatic dependency on
  `RUBRIC_VERSION` because the dim averages are pre-aggregated.

### Testing

- Pytest `test_recommend_ranking_deterministic.py` — fixture user
  history runs the ranking layer twice and asserts identical
  `recommended_mission_ids` (LLM disabled in this test via the
  `Settings.llm_enabled = False` fixture).
- Pytest `test_recommend_tie_break_stable.py` — two missions with
  identical scores must sort by mission_id asc.
- Pytest `test_recommend_cold_start.py` — zero submissions returns the
  ladder.
- Pytest `test_recommend_cache_invalidation.py` — new graded submission
  invalidates the `user_recommendations` row; next call recomputes
  ranking AND looks up new `llm_cache` entry.
- Pytest `test_recommend_prose_cache_hit.py` — second call with
  identical (weakest_dim, mission_ids) hits `llm_cache`, does NOT
  invoke the SDK (mocked client asserts zero calls).
- Pytest `test_recommend_prose_fallback.py` — SDK injected to raise;
  endpoint returns the templated fallback string with no exception
  bubbling; `llm_generation_failed` event recorded.
- Vitest `recommendation-strip.test.tsx` — renders 3 cards with the
  diagnosis copy.
- Playwright `recommendation-flow.spec.ts` — sign in, finish mission 01
  weakly on `agent_review`, navigate to profile, assert mission 02 is
  the top recommendation.

### Rollout

Two PRs:

1. **PR1** — migration 0026, recommendation engine, API endpoints,
   backfill of `expected_weak_dim`. The FE still reads
   `recommended_mission_ids` from the score report.
2. **PR2** — FE surfaces (profile strip, catalog chip, report CTA
   re-wire).

### Open decisions

- **Should the diagnosis copy be LLM-polished post-deterministic?**
  Resolved in this revision: **yes**, via the §0.4 cache. The
  templated fallback survives Bedrock outages so quality lift is
  free of an availability tradeoff. The cached output is what is
  served on the hot path; the LLM is never on the request critical
  path beyond the first cold-miss per (weakest_dim, mission_set).
- **Should the engine expose a "show me a stretch mission" affordance
  (one band harder than recommended)?** Defer; ships in P1-10
  (calibration transparency) as a natural pair.
- **Should the recommendation be shown to signed-out catalog
  visitors?** No. Recommendations are personal. Anonymous catalog
  shows the catalog flat.
- **Should we warm the cache for all 7 dimensions × top-3 mission
  combinations at deploy time?** Recommendation: yes — a tiny
  warm-up script (`scripts/warm_recommendation_cache.py`) runs once
  per `prompt_version` bump, generating one row per likely
  (weakest_dim, common-mission-set) pair. Eliminates cold-miss
  latency entirely for the common case.

---

## P1-3. LSP / IntelliSense in Monaco for sandbox languages

### Goal

Editing in the workspace feels meaningfully like editing in VS Code for
the three sandbox languages (TypeScript, Python, Go). Concretely:

- **Hover** shows type signatures.
- **Go-to-definition** jumps within the open workspace files.
- **Diagnostics** (red squiggles) surface type errors and obvious lints
  without the user having to run `tsc` / `mypy` / `go vet`.
- **Completion** suggests symbols visible in the open file's imports
  plus the workspace's local declarations.

This is not "feature parity with VS Code." It is the floor below which
the "real repositories" claim is empty — and it's the single largest
quality-of-life delta the active workspace user notices.

### Architecture — choice of where the LSP runs

Considered:

1. **In-browser LSP** (e.g., `pyright` compiled to Wasm,
   `monaco-typescript`). Tempting but: pyright-Wasm is large
   (~25 MB), file sync from sandbox is awkward, and Go's `gopls` has
   no Wasm target.
2. **API-server LSP**. One pyright per user per language; unbounded RAM
   in the FastAPI process; cross-user file isolation violated unless we
   reproduce the sandbox boundary inside the API container.
3. **In-sandbox LSP** (recommended). Each language server runs as a
   child process inside the user's per-session sandbox container,
   reading the user's files directly. The browser opens a WS to the
   API, which proxies LSP JSON-RPC into the sandbox over the existing
   stdio channel of the sandbox driver.

Option 3 is the right answer for three reasons:

- **Files live in the sandbox.** The LSP needs to walk them; doing it
  in-sandbox is zero-copy.
- **Isolation is already correct.** The sandbox is `--cap-drop=ALL`,
  `--network=none`, per-user. The LSP inherits all of that — no new
  attack surface.
- **Resource bounds match the sandbox lifecycle.** When the reaper
  kills the sandbox (30-min idle), the LSP dies with it. No
  per-language-server pool to manage in the API.

### Architecture diagram

```
Browser (Monaco + monaco-languageclient)
   │
   │  language client JSON-RPC over WS
   ▼
API container (FastAPI /lsp WS endpoint)
   │
   │  proxy JSON-RPC frames; do NOT inspect; do NOT cache
   ▼
Sandbox container (driver.spawn_lsp(language))
   │
   ├─ pyright-langserver --stdio        (Python sandboxes)
   ├─ typescript-language-server --stdio  (TypeScript sandboxes)
   └─ gopls serve                       (Go sandboxes)
```

The API does not parse LSP messages — it forwards bytes. This keeps the
API container stateless and the LSP transparent to mid-stream protocol
revisions.

### File-sync model

Monaco's in-memory buffers are authoritative on the FE; the sandbox
files on disk are authoritative on the BE. The sync is **save-driven**,
not edit-driven, for three reasons:

- LSP `textDocument/didChange` notifications already carry the in-memory
  buffer, so completions and diagnostics use the unsaved text without
  needing a sandbox-disk write on every keystroke.
- The save event (`PATCH /sessions/{id}/files/...`) already exists and
  already triggers a sandbox write; the LSP picks it up via its own
  file watcher.
- Edit-driven disk writes would burn sandbox FS quota and complicate
  the existing FileChange event log.

This means: hover / completion / inline diagnostics on unsaved text
work; *go-to-definition* across files works on saved content only.
Acceptable trade-off; documented in the help overlay.

### API surface

One new WS endpoint:

```
WS /api/v1/sessions/{session_id}/lsp
  Subprotocols: ['lsp.openagentdojo.v1']
  Query: ?language=python   (one of: python, typescript, go)
  Auth: same as the workspace WS (session cookie + CSRF on the
        handshake; identical to the existing event WS at
        apps/api/app/sessions/ws.py).
  Lifecycle:
    1. on open: API spawns the LSP process in the sandbox via
       driver.spawn_lsp(language). One LSP per (session_id, language).
    2. while open: framed JSON-RPC bytes forwarded both ways.
    3. on close (or sandbox reap): API sends shutdown LSP message,
       waits 2 s, kills the process.
  Errors:
    409 if a language server for this (session, language) is already
        running on a different WS (prevents double-spawn).
    404 if session is not active.
    503 if the sandbox is busy (apply-patch in flight).
```

The driver gains:

```python
async def spawn_lsp(handle: SandboxHandle, language: str) -> LSPProcess:
    """Start a language server inside the sandbox, return a handle
    that bridges stdin/stdout to the WS."""
```

Implementation reuses the existing `driver.run` boundary; the LSP just
happens to be a long-lived stdio process instead of a one-shot command.

### Frontend surface

```
[ apps/web/components/workspace/CodeEditor.tsx ]
    + on mount: open WS /sessions/{id}/lsp?language={file lang}
    + wire monaco-languageclient against the WS
    + show a small `// lsp · python` indicator in the editor footer
      with a state colour:
        green  = running, healthy
        amber  = cold-start, < 5 s since open
        red    = error, with a tooltip on hover

[ apps/web/lib/lsp/ ]                                   NEW
    client.ts          monaco-languageclient setup, retry-on-close
    framing.ts         JSON-RPC framing helpers
    diagnostics.ts     translates LSP Diagnostic → Monaco markers
```

Language detection is by file extension on the file the user is
viewing; switching files of a different language opens a second WS
lazily. A maximum of two LSPs run concurrently per session (most users
will only edit one language during a mission); a third file-language
soft-kills the oldest LSP.

### Resource bounds

| Language | Cold-start budget | RSS hard cap (sandbox cgroup) |
|---|---|---|
| pyright-langserver | 4 s | 256 MB |
| typescript-language-server | 3 s | 512 MB |
| gopls | 5 s (depends on go.mod size) | 384 MB |

The sandbox already enforces cgroup memory caps; the LSP inherits the
container's overall limit. We add a per-process memory log at the WS
proxy so an OOM is surfaced as a structured error to the FE (`{kind:
"lsp_oom", language}`) instead of a silent disconnect.

### Scoring interactions

The grader does NOT consume LSP events. The user accepting a
completion or hovering for a signature does not change the score. The
LSP is a productivity tool, not a measured behaviour.

Open question worth flagging: should `lsp_completion_accepted` count as
a verification-discipline-adjacent signal in a future rubric expansion?
Recommendation: no. The supervision signal we measure is *the user
verifying the agent's output*; LSP-driven productivity is the user's
own work, not their work supervising the agent.

### Edge cases

- **LSP crashes mid-session.** WS closes with `{code: 1011, reason:
  "lsp_crashed"}`. FE shows an amber chip with "LSP unavailable — retry"
  and surfaces a retry button. Editing continues without LSP support.
- **User switches files faster than the LSP starts.** The client
  queues the `textDocument/didOpen` until the WS opens; no race.
- **Sandbox apply-patch invalidates the LSP's file watcher.** The LSP
  re-indexes on the next save event — same as VS Code on a manual
  refresh.
- **User opens a file in a language we don't ship an LSP for** (e.g.,
  shell, yaml, markdown). The editor falls back to syntax highlighting
  only; no chip is shown. Documented in the help overlay.
- **A mission's repo is so large that `gopls` cold-starts past the
  budget.** The chip stays amber; completions are still served once
  the cold-start completes. Telemetry surfaces the slow start so we
  can right-size the cap.

### Testing

- Pytest `test_lsp_spawn_lifecycle.py` — spawn pyright in a fixture
  sandbox, assert healthy initialize handshake, shut down cleanly.
- Pytest `test_lsp_ws_proxy_frames.py` — bytes injected at the WS
  surface arrive byte-identical at the sandbox LSP stdin.
- Pytest `test_lsp_one_per_language.py` — second WS for the same
  (session, language) returns 409.
- Vitest `lsp-client.test.ts` — mocks the WS and asserts
  monaco-languageclient receives diagnostics.
- Playwright `lsp-completion.spec.ts` — open mission 02 (Python),
  trigger completion on `os.`, assert a result list.

### Rollout

Three PRs, gated by a feature flag (`features.lsp_enabled`):

1. **PR1** — Driver `spawn_lsp` + WS proxy + Python (`pyright`).
   Internal-only behind the flag.
2. **PR2** — TypeScript LSP. Flag still internal.
3. **PR3** — Go LSP (`gopls`). Flag flipped on for all users.

### Open decisions

- **Should we ship a fallback "in-browser monaco-typescript" so the
  editor at least feels alive even if the LSP WS fails?**
  Recommendation: yes for TypeScript specifically — monaco-typescript
  is already shipped as a Monaco bundle and the fallback path is cheap.
  For Python and Go, no fallback.
- **Should the LSP image embed `ruff` / `prettier` / `eslint`?**
  These are not LSPs; they are formatters. Defer; the workspace
  doesn't yet expose a "format on save" affordance and shipping it now
  risks the grader penalising churn the user didn't intentionally
  introduce.
- **Per-user RSS limit if multiple LSPs run.** Hard cap at 1 GB total
  per sandbox; track in cgroup. Document this; do not silently OOM.

---

## P1-4. Workspace notes / scratchpad

### Goal

A persistent, autosaved, markdown-aware scratchpad pane in the workspace.
The user can jot reasoning *before* prompting — converting "I'm thinking"
from a thought into a written artefact. The scratchpad is:

- **Per-session.** Each mission attempt has its own scratchpad; resets
  do not clear it (the user's thinking is theirs).
- **Private.** Not exposed in the public profile, the verify artefact,
  or the report. Visible only to the session owner.
- **Event-sourced.** Edits emit a debounced supervision event that the
  future scoring layer can consume. MVP does *not* score the
  scratchpad.

The product hypothesis: users who pre-write reasoning supervise more
deliberately. We ship the artefact and the events now; we measure
the hypothesis with telemetry before adding a scoring signal.

### Architecture

```
[ apps/api/alembic/versions/0027_session_notes.py ]
    + session_notes table (one row per session)
    + supervision_events.event_type accepts 'note.edited' and
      'note.viewed_during_prompt'

[ apps/api/app/sessions/notes.py ]                  NEW
    GET    /sessions/{id}/note               — read
    PUT    /sessions/{id}/note               — replace (debounced from FE)
    schemas + service layer

[ apps/api/app/sessions/events.py ]
    NoteEvents constants extended:
      'note.edited'                — debounced 2 s window
      'note.viewed_during_prompt'  — emitted by FE on prompt focus while
                                     the scratchpad has content

[ apps/web/components/workspace/ScratchpadPane.tsx ]
    A monaco-light editor (CodeMirror or a small Monaco instance in
    markdown mode) anchored to the bottom-right of the workspace,
    collapsible. Autosave via a 1.5 s debounced PUT.

[ apps/web/stores/workspaceStore.ts ]
    + scratchpadOpen: boolean (default: false; persisted in
      localStorage per (user, session) so the user's preference
      survives reloads)
```

### Data model (migration 0027)

```sql
CREATE TABLE session_notes (
    session_id   UUID PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
    body         TEXT NOT NULL DEFAULT '',
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Cap body length defensively.
ALTER TABLE session_notes
    ADD CONSTRAINT session_notes_body_length CHECK (length(body) <= 32768);
```

32 KB is generous enough for any reasonable mid-session scratch and small
enough that the per-row write cost stays cheap. The cap also bounds the
worst-case event payload.

The `supervision_events` table is unchanged structurally; the new event
types are declared in [`docs/schemas/event.schema.json`](docs/schemas/event.schema.json):

```json
{
  "event_type": "note.edited",
  "payload": {
    "type": "object",
    "required": ["bytes", "lines", "seconds_since_last_edit"],
    "properties": {
      "bytes": { "type": "integer", "minimum": 0 },
      "lines": { "type": "integer", "minimum": 0 },
      "seconds_since_last_edit": { "type": "integer", "minimum": 0 }
    }
  }
}

{
  "event_type": "note.viewed_during_prompt",
  "payload": {
    "type": "object",
    "required": ["bytes_at_view"],
    "properties": { "bytes_at_view": { "type": "integer", "minimum": 0 } }
  }
}
```

`note.edited` carries no text content — the body is in `session_notes`,
the event only carries an *indicator that an edit happened*. This is
load-bearing for privacy: the supervision-event log is exported to the
data-portability bundle (P0_DESIGN P0-6) and to the replay artefact
(P1-6 below); we do not want the scratchpad text inlined into every
downstream artefact.

### API surface

```
GET /api/v1/sessions/{session_id}/note
  Auth: owner of session.
  Response: { body: string, updated_at: iso8601 }

PUT /api/v1/sessions/{session_id}/note
  Auth: owner of session, session.status == 'active'.
  Body: { body: string }   — server enforces 32 KB limit, returns 413 otherwise
  Side-effects:
    1. UPSERT session_notes row.
    2. Emit supervision_event note.edited with debounced metadata.
       The events module coalesces note.edited events from the same
       session within a 30 s rolling window into a single event (with
       the latest payload).
  Response: { body, updated_at }
```

Coalescing within 30 s is the right balance: it produces a meaningful
event stream (you can see when the user was thinking) without
exploding row count from every keystroke.

### Frontend surface

The workspace shell gains a fourth dock area. The existing layout has
three primary docks (FileTree, Editor/Terminal, AgentChat/Timeline).
The Scratchpad is a fourth, collapsible, anchored to the bottom of the
AgentChat column:

```
┌──── File Tree ────┬──── Editor ────┬──── Agent Chat ────────────┐
│                   │                │                            │
│                   │                │   prompt 1                 │
│                   │                │   prompt 2                 │
│                   │                │                            │
│                   │                ├────────────────────────────┤
│                   │                │ // notes  ▴ collapse       │
│                   │                │ - check the cookie         │
│                   │                │   expiration logic         │
│                   │                │ - hidden tests probably    │
│                   │                │   check Date.now()         │
└───────────────────┴────────────────┴────────────────────────────┘
```

A small `[+] // notes` button at the bottom of AgentChat toggles open
when collapsed. Open state persists per (user, session) in
localStorage. The pane uses CodeMirror 6 in markdown mode (the existing
Monaco bundle is too heavy for a third in-page editor and we already
have CodeMirror as a transitive dep for the diff viewer).

The scratchpad emits a `note.viewed_during_prompt` event when the agent
chat composer is focused AND the scratchpad has > 0 bytes. This is the
key signal: it lets us correlate "did the user have notes open while
prompting" with eventual score.

### Scoring interactions

**MVP: zero.** The scratchpad is not scored.

Rationale: scoring "you wrote notes" rewards the *appearance* of
deliberation, not the substance. We need telemetry (the
`note.viewed_during_prompt` count, correlated with score) before we
know whether the habit is causally related to good supervision. A
future PR (post-launch) can add a small Prompt-Quality signal *if* the
correlation is real.

Concretely: `note.edited` and `note.viewed_during_prompt` are emitted,
land in the supervision_events log, and are visible in the post-mortem
timeline (P0-2). The score engine ignores them.

### Coaching reflection in the post-mortem (LLM-augmented)

This is the highest-leverage LLM use site in the platform. When the
user opens the post-mortem walkthrough (P0-2) for a submission whose
session had a non-empty scratchpad, the walkthrough renders an
additional section:

```
┌─────────── // what you wrote vs. what you did ──────────────┐
│                                                             │
│ At 00:01:14 you wrote in your notes:                        │
│   > "check the cookie expiration logic"                     │
│                                                             │
│ But your first prompt at 00:02:31 asked the agent to fix    │
│ the "session validation bug" without mentioning expiration. │
│ The agent picked up a presence check instead.               │
│                                                             │
│ Your notes pointed at the load-bearing question — your      │
│ prompt didn't carry it through.                             │
│                                                             │
│ Next time: paste the bullet from your notes verbatim into   │
│ the prompt. The thinking you did is half the supervision.   │
└─────────────────────────────────────────────────────────────┘
```

This is **the** moment the product fulfils its pedagogical thesis —
"train the eye that catches them." The user can see in writing the
gap between their reasoning and their action.

#### Architecture

The coaching reflection is generated post-grading and cached. It runs
under the §0.4 discipline:

```
[ apps/api/app/reports/coaching.py ]                  NEW
    generate_coaching_reflection(submission_id) -> str | None
      1. Loads the session_notes body for the session.
      2. If empty: returns None (the post-mortem section is hidden).
      3. Otherwise: hashes (notes_sha256, events_sha256,
         mission_id, mission_version) → content_hash.
      4. Calls llm.cache.get_or_generate(
             domain="scratchpad_coaching",
             content_hash=content_hash,
             prompt_version=PROMPT_VERSION,
             model_id="claude-sonnet-4-6",
             generator=_run_coaching_prompt,
         )
      5. Returns the cached or freshly-generated string.

[ apps/api/app/llm/prompts/scratchpad_coaching.md ]   NEW
    Jinja2 template. Inputs:
      - the scratchpad body (verbatim)
      - a normalised event timeline (event_type + offset +
        truncated payload summary for the supervision-relevant
        events: prompt.submitted, agent.responded, patch.applied,
        command.run, diff.opened, submission.requested)
      - the mission's failure_mode + ideal_solution.md
      - the post-grading score_report (dimensions only — no
        evidence_event_ids text)
    Instructions:
      - Output 3-6 sentences in second person.
      - Cite ONE specific quote from the notes and ONE specific
        event id from the timeline; the FE renders those as anchors
        into the timeline + scratchpad surfaces.
      - If the notes are off-topic or empty of useful signal, say so
        honestly — do not invent a coaching point.

[ apps/web/components/report/CoachingReflection.tsx ]   NEW
    Renders the section. Lazy-loaded: the request only fires when the
    user scrolls the section into view (the LLM call is expensive
    cold-cache, and many post-mortem visits skim only the diff).
```

#### API surface

```
GET /api/v1/submissions/{submission_id}/coaching
  Auth: submission owner only — coaching content embeds quotes from
        the user's private scratchpad and must never be shared.
  Response:
    {
      reflection: string | null,
      anchored_event_id: int | null,
      anchored_note_quote: string | null,
      cached: bool,
      generated_at: iso8601
    }
  null reflection means "no scratchpad content was present" — the FE
  omits the section.
  Errors:
    503 with `{code: "llm_unavailable"}` if the cache misses AND the
    SDK fails. The FE hides the section quietly — coaching is value-add,
    not load-bearing for the report.
```

#### Privacy & data flow

The scratchpad body is **sent to the LLM** for this domain. This is
the only LLM use site that sees user-private text. The privacy posture:

- The Privacy Policy (P0_DESIGN P0-5) is updated to disclose this
  explicitly, naming AWS Bedrock as the processor.
- The `/account/privacy` page (P0-6) ships an opt-out toggle:
  `Coaching reflections (sends scratchpad text to AWS Bedrock)`. When
  off, the section is hidden regardless of cache state. The toggle
  defaults to ON for new accounts — consistent with the existing
  analytics-by-default-after-consent posture from P0-5, and gated by
  the cookie banner accept flow.
- Cached `llm_cache` rows for `scratchpad_coaching` are deleted by the
  account-deletion job (P0_DESIGN P0-6) via a lookup by content_hash
  → it's the only domain whose hash includes user-text inputs. The
  worker does `DELETE FROM llm_cache WHERE domain='scratchpad_coaching'
  AND content_hash IN (... user's notes hashes ...)`.

#### Model selection rationale

`claude-sonnet-4-6` (per §0.4.4) — not Haiku because the model has to
align a fuzzy text artefact (user's notes) against a structured event
timeline. Sonnet's pattern-matching across heterogeneous inputs is the
load-bearing capability; Haiku produced too-generic outputs in early
prompt eng experiments. Opus is overkill and the latency hurts the
post-mortem-load UX.

#### Edge cases

- **Notes contain only stray characters / no usable signal.** The
  prompt instructs the model to say so honestly. The FE renders the
  reflection as-is; users see "Your notes didn't carry a clear
  thread. Try writing the bug hypothesis in one sentence before
  prompting" — itself coaching.
- **User opts out mid-session.** Subsequent renders hide the section.
  The cached row stays in `llm_cache` (it's no-PII-by-key — the row
  is keyed by content hash, not user id) until the next account
  deletion sweep.
- **Notes contain code snippets.** Treated as text. The prompt
  template tells the model not to attempt to *evaluate* the code, only
  reflect on whether the user's noted hypothesis matched their
  subsequent prompt.
- **Replay artefact (P1-6) embedding.** The coaching reflection is
  NOT embedded in the replay artefact. The replay is a credentialing
  surface; the reflection is teaching content meant for the user's
  eyes only.

#### Testing

- Pytest `test_coaching_skipped_for_empty_notes.py` — empty
  scratchpad returns reflection=None.
- Pytest `test_coaching_cache_key_includes_notes.py` — same notes
  produce the same content_hash; one-char edit produces a different
  hash.
- Pytest `test_coaching_redacted_on_account_delete.py` — deletion
  worker removes the `scratchpad_coaching` rows.
- Vitest `coaching-reflection.test.tsx` — anchor click into timeline
  and scratchpad surfaces.
- Manual: a small fixture set of 5 (notes, timeline) pairs is checked
  in at `apps/api/tests/fixtures/coaching/*.json` with the expected
  output bytes. The test asserts byte-equality against the cache
  contents for the same (content_hash, prompt_version, model_id) —
  this is the "no LLM drift" guard.

### Edge cases

- **User pastes a 50 KB block.** Server returns 413; FE shows
  "Scratchpad is full — copy somewhere safe and trim."
- **User opens the workspace, makes 30 KB of notes, then the sandbox
  is reaped.** The notes survive — they live in postgres, not the
  sandbox. Resume re-opens the same notes.
- **User retries the mission (new session, P0-3).** The new session
  has an empty scratchpad. Previous attempt's notes are still
  accessible via the previous session's report page (read-only, since
  the previous session is graded).
- **Notes after `session.gave_up_at`.** Read-only — the session is
  no longer active. The PUT endpoint returns 409 with
  `code: "session_not_active"`.
- **User deletes account (P0_DESIGN P0-6).** `session_notes`
  cascade-deletes via FK.
- **Scratchpad open during a `session.reset` (P0-12).** Reset does NOT
  clear notes. Reset clears workspace files; notes are the user's
  reasoning, which survives.

### Privacy

- The scratchpad body is treated as PII-grade. The data-export bundle
  (P0_DESIGN P0-6) includes a `session_notes.jsonl` file.
- The replay artefact (P1-6 below) includes the scratchpad body only
  if the requester is the submission owner; the anonymous verify page
  (P0-11) never includes it.
- The supervision_event payload carries metadata (bytes, lines) but
  NOT the text — separate channels for content and structure.

### Testing

- Pytest `test_session_notes_crud.py` — put/get round-trip; 32 KB cap
  enforced; ownership enforced.
- Pytest `test_note_edited_event_coalesce.py` — five PUTs within 30 s
  produce one event; the sixth (35 s later) produces a second.
- Pytest `test_note_survives_session_reset.py` — POST /sessions/.../reset
  leaves session_notes untouched.
- Vitest `scratchpad-pane.test.tsx` — autosave debounce; collapse-state
  persists in localStorage.
- Playwright `scratchpad-flow.spec.ts` — open mission → write notes →
  submit → notes appear on the report's timeline as
  note.viewed_during_prompt events.

### Rollout

Single PR for the backend + storage + events; single PR for the FE
pane. No feature flag — the pane is opt-in by collapse state, default
collapsed for new users.

### Open decisions

- **Markdown rendering or raw text?** Recommendation: raw text only in
  MVP. Markdown preview adds a re-render dependency and the value-add
  is small. Revisit if users ask.
- **Should the scratchpad be visible in the post-mortem walkthrough
  (P0-2) for the user?** Yes — read-only, as a separate tab next to
  the existing prompts. It's the user's reasoning at the time; useful
  to re-read with the benefit of hindsight.
- **Should notes carry across attempts of the same mission?**
  Recommendation: no, each attempt is a clean slate (notes are
  attempt-scoped reasoning). But the previous attempt's notes are
  always accessible from the previous report page.

---

## P1-5. User-vs-ideal diff side-by-side in the report

### Goal

Promote the post-mortem walkthrough's three-way diff (P0_DESIGN P0-2)
from a stacked block layout to a proper, ergonomic, **synchronously
scrolling, anchor-aligned, side-by-side** view. The data is already
shipped by P0-2; this item is the rendering and ergonomics upgrade
that makes the post-mortem actually readable on a typical screen.

Concretely:

1. The three layers (user's final diff, ideal solution, agent's first
   patch) lay out as **two side-by-side panes plus a collapsible
   third strip** — the original P0-2 design.
2. The user's diff and the ideal diff scroll **in lockstep** when
   their changed-line anchors align (same file, same hunk).
3. **Load-bearing lines** — the line that flips the failure mode —
   carry a left-margin marker that travels with the line as the user
   scrolls.
4. The diff respects the existing dark-on-dark dojo aesthetic; no new
   colour tokens.

### Architecture

This is FE-only. The API contract from P0-2 (returning
`ideal_solution_diff` and `agent_patch_diff` alongside `submission`)
is unchanged.

```
[ apps/web/components/workspace/DiffViewer.tsx ]
    Existing component already supports `mode: 'split'`. Extended to
    a `mode: 'three-way-synchronised'` that takes three diffs and
    pairs them on (file, hunk_anchor).

[ apps/web/components/report/PostMortemWalkthrough.tsx ]
    Re-shaped from "stacked DiffViewer instances" → "ThreeWayDiff
    container that owns scroll sync and load-bearing-line overlay."

[ apps/web/components/report/ThreeWayDiff.tsx ]                NEW
    Owns the dual-pane layout, scroll sync, anchor overlay, and
    third-strip collapse. Reuses DiffViewer internally for each pane.

[ apps/web/components/report/LoadBearingLineMarker.tsx ]       NEW
    A small left-margin indicator with hover text "this line is the
    one the agent got wrong" / "this line is the fix you missed."
    Position computed from the diff anchor plus the score_report's
    `critical_moments[].event_id` (already a P0-2 field).
```

### Data contract

No new fields. The P0-2 critical-moment shape already includes the
`event_id` that maps back to a `patch.applied` or `agent.responded`
event whose payload carries the affected file path and line range.
This file path + line range is enough to mark the load-bearing line on
both the user and ideal panes (the diff already knows which lines
changed).

If a critical moment's event payload does not have a usable line
anchor (some moments are "submitted without verification" — no line),
the marker is suppressed for that moment. The diff still renders.

### Frontend layout

```
┌─────────────── // what the agent did vs. what was expected ──────────────┐
│                                                                          │
│  ┌────────── your submission ──────────┬─── ideal solution ─────────────┐│
│  │ session.ts                          │ session.ts                     ││
│  │                                     │                                ││
│  │  12 │ if (cookie === undefined) {   │ 12 │ if (!session ||           ││
│  │  13 │   return null;                │ 13 │     !session.isValid(    ││
│  │  14 │ }                             │ 14 │      Date.now())) {      ││
│  │  15 │ return cookie["uid"];         │ 15 │   return null;           ││
│  │ ◄ load-bearing                      │ 16 │ }                        ││
│  │                                     │ ◄ load-bearing                ││
│  │                                     │                                ││
│  └─────────────────────────────────────┴───────────────────────────────┘│
│                                                                         │
│  [▸ show the agent's original patch (what it first proposed)]           │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

Click "show the agent's original patch" expands a third strip *below*
the dual pane (not as a third column — three columns at workspace
width is unreadable). The strip's scroll is independent because its
anchor space doesn't align with the user's diff anyway.

### Scroll synchronisation

A small custom hook `useSynchronisedDiffScroll(refA, refB)`:

```typescript
function useSynchronisedDiffScroll(refA, refB, anchorMap) {
  // anchorMap: array of { aLine, bLine } pairs at hunk boundaries
  // On scroll of refA, find the nearest aLine; scroll refB to bLine
  // with a damped approach to avoid bounce.
  // Same for refB → refA. Cycle-protected with a tick guard.
}
```

The anchor map is computed once on mount by walking both diffs in
parallel: each common hunk header becomes an anchor pair. Lines
between anchors are interpolated linearly. This avoids the naive
"scroll by % of total height" which mis-aligns when the two diffs
have wildly different lengths.

### Mobile / narrow viewport

Below ~960 px the side-by-side collapses to a tabbed view ("Your
submission · Ideal solution · Agent's original patch") — same control,
sequential rather than simultaneous. The workspace itself is
desktop-only ([plan §26](IMPLEMENTATION_PLAN.md)), but the **report**
is mobile-targeted (FEATURE_GAPS P1-15), so this matters.

### Edge cases

- **User's submission diff is empty** (give-up before any edits).
  Left pane renders an empty-state ("no edits submitted"); right pane
  renders normally; scroll sync is a no-op.
- **Ideal diff missing** (legacy mission without `ideal_solution.diff`).
  P0-2's validator prevents this for non-tutorial missions; defensive
  branch renders the markdown ideal solution as a fallback.
- **Both diffs reach the same file but the line numbers differ by
  many lines** (large refactor on one side). The anchor map still
  pairs hunk headers; lines mid-hunk are approximated. Acceptable
  because the load-bearing lines themselves are explicit, not
  interpolated.
- **Three identical critical moments on the same line.** Render one
  marker (deduped by line); tooltip aggregates the moments.

### Testing

- Vitest `three-way-diff.test.tsx` — given two fixture diffs with a
  shared file, anchor map pairs the hunks correctly.
- Vitest `scroll-sync.test.tsx` — scrolling refA triggers refB to
  scroll to the expected paired line.
- Playwright `report-walkthrough.spec.ts` (existing from P0-2)
  extended to scroll the user pane and assert the ideal pane follows.

### Rollout

Single PR. No backend changes. No data migration. Behind a small FE
feature flag (`features.three_way_diff_v2`) for one release so the
old stacked layout remains the rollback path; then flag removed.

### Open decisions

- **Should we also align the timeline scrubber (P0-2's "critical
  moment at 00:03:42") to the load-bearing line marker on the diff?**
  Recommendation: yes — clicking a critical moment in the timeline
  scrolls *both* diff panes to the affected lines. This is the unified
  post-mortem experience the user actually needs.
- **Show inline character-level diff on load-bearing lines?**
  Recommendation: yes for token-level (split on whitespace +
  punctuation). Character-level on token-distinct lines reads as
  noise.

---

## P1-6. Supervision-event JSON export ("the replay artifact")

### Goal

Every graded submission has a **single canonical, deterministic,
signed** export that:

- Captures the full supervision event stream that produced the score,
- Embeds the score report and the verification envelope (P0-11),
- Is byte-identical across replays (load-bearing for the determinism
  promise),
- Doubles as the candidate's proof-of-work (a recruiter can hand the
  zip to anyone with the open replay tool — see "Future use" — and
  re-derive the score independently),
- Doubles as the foundation for a future score-appeal flow
  (FEATURE_GAPS P1-7).

Two endpoints, one artefact shape:

1. **`GET /api/v1/submissions/{id}/replay.json`** — the canonical JSON
   form, served inline for programmatic consumers.
2. **`GET /api/v1/submissions/{id}/replay.zip`** — the same data
   packaged with the mission manifest pointer, the user-final diff,
   and a README — suitable for offline archival.

### Architecture

```
[ apps/api/app/reports/replay.py ]              NEW
    build_replay(submission_id) -> ReplayArtifact
        — pure function; identical inputs → identical bytes
    The "pure" property is enforced by canonicalisation rules below.

[ apps/api/app/reports/router.py ]
    + GET /submissions/{id}/replay.json
    + GET /submissions/{id}/replay.zip
    Both share the same build function; only the serialisation differs.

[ apps/web/components/report/ReportShareDropdown.tsx ]
    The dropdown introduced in P0-11 gains:
      [ Download replay JSON ]   — for the technically curious
      [ Download replay zip  ]   — for archival
```

### Replay artefact shape

```json
{
  "schema_version": 1,
  "kind": "openagentdojo.replay.v1",
  "submission_id": "7c4123ab-…",
  "envelope": {
    "schema_version": 1,
    "submission_id": "7c4123ab-…",
    "handle": "jane",
    "display_name": "Jane Doe",
    "mission_id": "auth-cookie-expiration",
    "mission_version": 1,
    "rubric_version": "v1",
    "total_score": 78,
    "effective_max": 100,
    "missed_failure_mode": false,
    "score_cap_reason": null,
    "proctored": false,
    "attempt_index": 2,
    "graded_at": "2026-05-23T18:42:11Z"
  },
  "envelope_signature": "0xde…91",
  "score_report": { /* the canonical score_report JSONB, sorted keys */ },
  "events": [
    {
      "id": 1,
      "event_type": "session.started",
      "occurred_at": "2026-05-23T18:31:02.123Z",
      "payload": { /* sorted keys */ }
    },
    /* every supervision_events row for this session, ordered by
       (occurred_at ASC, id ASC) — same ordering the grader uses */
  ],
  "final_diff": "diff --git a/src/session.ts b/src/session.ts\n…",
  "mission_pointer": {
    "id": "auth-cookie-expiration",
    "version": 1,
    "manifest_sha256": "abc…",
    "repo_pack_id": "fullstack-auth-demo",
    "repo_pack_sha": "def…"
  },
  "exported_at": "2026-05-27T12:00:00Z",
  "exported_at_omitted_from_signature": true,
  "replay_signature": "0xab…cd"
}
```

`replay_signature` is `hmac_sha256(VERIFY_SECRET, json.canonical(
{everything except exported_at and replay_signature itself}))`.
`exported_at` is *not* part of the signature so the same submission
re-exported a year later produces the same signature — only the
non-signed `exported_at` field changes. This preserves the determinism
property while still recording when the bundle was produced.

### Canonicalisation rules (load-bearing)

The artefact is *byte-deterministic* across exports of the same
submission. The rules:

1. JSON keys are sorted ASCII-lexicographic at every nesting level.
2. JSON serialisation uses `ensure_ascii=False`, `separators=(",",
   ":")`, no trailing whitespace.
3. Events are sorted by `(occurred_at_iso8601, id ASC)`. The id
   tie-break covers events at the same instant (microsecond
   ordering); the iso8601 uses Z-suffix UTC with microsecond
   precision.
4. The mission_pointer hashes a canonicalised manifest, not the
   manifest YAML (YAML serialisation is not deterministic).
5. The `final_diff` is the diff between the mission's
   `initial_commit` and the submission's recorded `final_tree_sha`,
   regenerated using `git diff --no-color --no-ext-diff --no-renames`
   — pinned flags so output is identical across libgit2 / cli
   versions.
6. `exported_at` is the only non-deterministic field and is excluded
   from the signature.

A test (`test_replay_determinism.py`) builds the artefact twice for the
same fixture submission and asserts byte-equality of everything except
`exported_at`.

### API surface

```
GET /api/v1/submissions/{submission_id}/replay.json
  Auth:
    - submission owner: full artefact
    - share-token holder: full artefact except `events[].payload`
      fields that contain prompt text (privacy: shared reports do not
      leak the user's prompts unless the owner opted in)
    - anonymous: 404
  Response: application/json; cached aggressively (immutable;
            ETag derived from replay_signature)

GET /api/v1/submissions/{submission_id}/replay.zip
  Auth: same matrix as the JSON variant.
  Response: application/zip stream
    arena-replay-{submission_id_short}-{graded_at}.zip
    ├── replay.json
    ├── final.diff
    ├── README.md      — human-friendly explanation + verification
                         instructions
    └── verify.html    — a static HTML page (no JS) that re-renders
                         the envelope + signature; user can open it
                         locally to confirm the bundle without going
                         online
```

The zip is built on the fly (no caching; small bundles, < 100 KB
typical). The JSON is cached at the CDN keyed by submission_id +
schema_version.

### Frontend surface

The report share dropdown introduced in P0-11 grows:

```
┌──────────────────────────────┐
│ Copy share link  (30d expiry)│
│ Download PDF                 │
│ Download PNG  (LinkedIn)     │
│ ─────────────────────        │
│ Download replay (JSON)       │  ← NEW
│ Download replay (ZIP)        │  ← NEW
│ ─────────────────────        │
│ Open verification page →     │
└──────────────────────────────┘
```

In the `/account` Data tab (P0_DESIGN P0-6) the per-submission row
gains a `[ Replay ]` button next to the existing `[ PDF ]` and
`[ Share ]` buttons.

### Privacy posture

The replay artefact is the most data-rich export the platform produces.
The posture:

| Field | Owner sees | Share-token holder sees | Anonymous |
|---|---|---|---|
| envelope | ✔ | ✔ | n/a |
| score_report (dimensions, signals) | ✔ | ✔ | n/a |
| events (event_type, occurred_at) | ✔ | ✔ | n/a |
| events.payload (prompt text, command output) | ✔ | redacted to `{redacted: true, byte_count: N}` | n/a |
| final_diff | ✔ | ✔ | n/a |
| mission_pointer | ✔ | ✔ | n/a |
| scratchpad body (P1-4) | ✔ | NOT INCLUDED | n/a |

This matrix is documented in the Privacy Policy (P0_DESIGN P0-5) and
inline in the zip's `README.md`.

### Scoring interactions

None at write time. The replay is a read-only artefact derived from
already-persisted state.

A future score-appeal flow (FEATURE_GAPS P1-7) can post a replay back
to the API and ask "re-grade this." The deterministic grader will
either reproduce the exact score (signature unchanged) or return a
calibrated explanation of any drift (rubric version bumped, mission
updated, etc.). That's the foundation P1-6 lays.

### Edge cases

- **Submission with `score_cap_reason="gave_up"`**: replay shows
  honestly. The cap is in the envelope.
- **Submission whose mission was edited after grading**:
  `mission_pointer.version` and `manifest_sha256` reflect the
  state-at-grading-time, not current. A consumer that wants to
  re-grade against the current mission sees the version mismatch and
  can decide.
- **Submission whose user was deleted (P0_DESIGN P0-6)**: replay still
  works; the envelope's `handle` and `display_name` are tombstoned.
  Owner authentication fails (user is gone), so only share-token
  holders can fetch — appropriate.
- **VERIFY_SECRET rotated**: replays signed under the old secret still
  verify against the rotation table maintained alongside the secret.
  Same posture as P0-11 verify envelopes.
- **Schema version bump**: a future `schema_version: 2` is a new
  format; old replays remain consumable via the schema_version field.
  The verify HTML in the zip is version-aware.
- **Replay download under rate limit pressure**: the JSON variant is
  cached at the CDN by ETag; the zip is built per request but cheap.
  No per-user rate limit needed; share-token holders are bounded by
  the existing share-token rate limit.

### Testing

- Pytest `test_replay_determinism.py` — build the artefact twice from
  the same submission; assert byte-equality across everything except
  `exported_at`.
- Pytest `test_replay_signature_verifies.py` — load the artefact,
  recompute the canonical hash, verify the signature with
  VERIFY_SECRET.
- Pytest `test_replay_share_token_redaction.py` — fetch with a share
  token; assert prompt text is replaced with the redaction marker.
- Pytest `test_replay_zip_contains_verify_html.py` — open the zip,
  assert `verify.html` is present and self-contained (no external
  references).
- Vitest `replay-dropdown.test.tsx` — dropdown renders the two new
  entries; click triggers the right endpoint.
- Playwright `replay-export.spec.ts` — sign in → report → download
  ZIP → unzip in temp → re-derive signature with a small node script.
- A canonical fixture submission is checked in at
  `apps/api/tests/fixtures/replay_canonical.json` and the test
  enforces equality byte-for-byte. Any change to the canonicalisation
  rules requires that fixture to be regenerated explicitly — the
  test failing is the desired warning.

### Rollout

Two PRs:

1. **PR1** — backend: replay builder + JSON endpoint + tests + the
   canonical fixture. No FE.
2. **PR2** — zip endpoint + verify.html + FE dropdown entries.

### Open decisions

- **Should the zip include the mission's `agent_patch.diff` so the
  consumer can fully replay grading offline?** Strongly yes — but
  requires shipping mission content alongside, which is a licensing
  surface. Recommendation: include with a `mission_license` field in
  the zip's README pointing at the repo's LICENSE (P0-13's Apache 2.0,
  if adopted). If the team picks a non-OSS license, exclude the
  agent_patch and ship only the envelope + events.
- **Should the artefact include the user's scratchpad body?** No, by
  default — see the privacy matrix. An "include scratchpad" toggle
  in the dropdown for the owner-only path could be added later; ship
  the artefact without it first.
- **JSON-LD context for the envelope?** Tempting (machine-friendly
  schema discovery), but defer — the JSON Schema URL in
  `docs/schemas/replay.schema.json` is sufficient.
- **Should a third-party re-grader exist as a published package?**
  This is the FEATURE_GAPS P1-7 territory (score-appeal). The replay
  artefact is sufficient infra to support it; the actual re-grader
  publishes later.

---

## A. Dependency graph

```
P1-1 (catalog expansion)   ─── independent
P1-2 (recommendations)     ─── reads P1-1's expected_weak_dim field;
                                must ship after P1-1's manifest schema
                                delta merges (the backfill works without
                                new Go missions — only the field matters)
P1-3 (LSP)                 ─── independent of the other P1s; touches
                                sandbox driver, isolated module
P1-4 (scratchpad)          ─── independent; touches workspace shell
P1-5 (three-way diff)      ─── depends on P0-2 (post-mortem walkthrough)
                                shipping; pure FE polish on top
P1-6 (replay artefact)     ─── reads P0-11's verification envelope and
                                signature; ships after P0-11 lands

Recommended ship order (minimises risk + maximises early value):
  §0.4 (LLM cache module + migration 0029 + Bedrock client wiring)
→ P1-1 (PR1: manifest schema delta + migration 0025)
→ P1-2 (PR1: backend engine + endpoints; LLM prose via §0.4)
→ P1-1 (PR2/PR3: Go pack + missions; LLM-assisted authoring lands
        with PR3 as an opt-in scaffold flag)
→ P1-2 (PR2: FE surfaces)
→ P1-4 (scratchpad storage + events; coaching reflection lands in a
        follow-up PR once the §0.4 cache warm-up is verified)
→ P1-5 (diff polish — depends on P0-2 already)
→ P1-6 (replay artefact — depends on P0-11 already)
→ P1-3 (LSP — last because it is the riskiest)
```

§0.4 must land before any LLM-augmented surface ships — it's the
shared substrate. The recommendation engine (P1-2) is the first
consumer, and its rollout is the natural shakedown of the cache +
fallback path.

P1-3 last is deliberate: the LSP path crosses the sandbox boundary in
a long-lived stdio way that is genuinely novel. Ship it with the most
operational headroom.

---

## B. What stays the same

All the architectural invariants from
[P0_DESIGN.md §B](P0_DESIGN.md) and
[P0_DESIGN_11_13.md §A](P0_DESIGN_11_13.md) continue to hold:

- **Determinism on the grading path.** No P1 introduces an LLM call to
  the grader. The recommendation **ranking** (P1-2) is deterministic
  by construction; the replay artefact (P1-6) is byte-identical
  across replays; the LSP (P1-3) is a productivity tool with zero
  scoring impact. LLM-polished prose (recommendation diagnosis,
  coaching reflection) is allowed only via the §0.4 cache and never
  on a hot ranking, scoring, or signing path — see §0.1 for the
  precise rule.
- **Cached-LLM determinism.** Every LLM use site reads through
  `apps/api/app/llm/cache.get_or_generate(...)`. The cache row is
  the byte-deterministic artefact downstream signatures hash against;
  the live SDK call is only on cold miss. A `prompt_version` bump
  invalidates and is treated as an operator action equivalent to
  `RUBRIC_VERSION` bumping — re-judgement campaigns run on the same
  schedule and the same machinery.
- **Event-sourcing.** P1-4 adds `note.edited` and
  `note.viewed_during_prompt` to the canonical event catalogue. P1-6
  *consumes* the event log but does not augment it. No LLM use site
  writes to `supervision_events`.
- **Mission manifest as content contract.** P1-1 adds `tags`,
  `repo_pack_id`, and `expected_weak_dim` — all additive,
  CI-validated. The LLM-assisted authoring scaffold writes to a
  `_draft/` subdirectory excluded from the validator until a human
  promotes the drafts; missions cannot ship LLM-authored without
  review. The validator (`pnpm validate:missions`) catches drift and
  blocks merge on a remaining `_draft/`.
- **Sandbox isolation.** P1-3 introduces a long-lived stdio process
  inside the sandbox; this is structurally identical to the existing
  `driver.run` path (rootless, network=none, cgroup-capped). No new
  privileges granted. The LSP image does not include the Anthropic
  client — LLM calls are API-server-side only, never from sandbox.
- **Process-only score preview during sessions.** P1-2's
  recommendations only render *post-submit* or *out-of-session* (on
  profile / catalog). The in-session workspace remains scoring-blind.
  The coaching reflection is post-grading-only.
- **Private supervisor-event payloads.** P1-6's share-token redaction
  preserves the existing "the user owns their prompts" posture. The
  coaching reflection (P1-4) is the *only* LLM use site that receives
  user-private text (the scratchpad body), is gated by an explicit
  opt-out, is disclosed in the Privacy Policy as a Bedrock data flow,
  and is purged on account deletion.
- **Canonical replay invariant.** P0-11's envelope and P1-6's replay
  artefact share the same canonicalisation discipline; one fixture
  test guards both. LLM-polished prose is *not* part of the signed
  bytes — the persisted `recommended_mission_ids` is the ranking
  output, never the cached prose, so rotating prompt templates does
  not invalidate signatures.

---

## C. Telemetry rollup

The full set of new telemetry events introduced by P0 + P1 to date,
re-listed here for the team to register at the analytics surface
([apps/web/lib/telemetry.ts](apps/web/lib/telemetry.ts)):

```
P0-1   tutorial_step_completed, tutorial_dismissed
P0-2   critical_moment_clicked, post_mortem_expanded
P0-3   attempt_retry_clicked, your_attempts_strip_shown
P0-4   give_up_clicked, give_up_confirmed
P0-5   consent_banner_shown, consent_accepted, consent_revoked
P0-6   account_export_requested, account_delete_scheduled,
       account_delete_cancelled, account_email_change_requested
P0-11  report_render_requested, report_render_succeeded,
       report_render_failed, report_verified
P0-12  session_reset_requested, session_reset_completed
P0-13  (none — docs)

P1-1   catalog_filter_used, roadmap_viewed
P1-2   recommendation_shown, recommendation_clicked
P1-3   lsp_session_started, lsp_completion_accepted, lsp_error
P1-4   scratchpad_opened, scratchpad_edit_persisted,
       scratchpad_viewed_during_prompt   (mirrors the supervision event),
       coaching_reflection_shown, coaching_reflection_anchor_clicked
P1-5   three_way_diff_synced_scroll, load_bearing_line_hovered
P1-6   replay_export_requested, replay_export_succeeded,
       replay_export_failed

§0.4  llm_cache_hit, llm_generation_succeeded, llm_generation_failed
      (cross-cutting; fired by every LLM use site)
```

All telemetry events respect the P0-5 consent posture: nothing fires
unless `getConsent().analytics === true`. The three §0.4 LLM events
are an exception when emitted server-side for *operational* purposes
(cost monitoring, cache-hit rate, error rate) — they carry no PII
(domain, model_id, token counts, error class) and are emitted to the
backend metrics surface (Prometheus / OTEL) rather than the
analytics-event pipeline; this is consistent with the existing
`event_publish_failures_total` Prometheus counter pattern.

---

— design authored against branch `codex/goal`, 2026-05-27.
