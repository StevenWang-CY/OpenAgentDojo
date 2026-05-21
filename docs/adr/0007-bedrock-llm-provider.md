# ADR 0007: Anthropic via AWS Bedrock as Default LLM Provider

- Status: Accepted
- Date: 2026-05-21
- Deciders: AgentSupervisor Arena team

## Context

The platform's LLM use is narrow and optional: rewriting templated agent prose for tone (see [ADR 0002](./0002-deterministic-agent.md)). Even so, we need a provider strategy because:

1. Prod and local-dev have different access realities. Prod uses an AWS-issued bearer token; local-dev uses a direct Anthropic API key.
2. We don't want every call site branching on `if BEDROCK: ... else: ...`.
3. Bedrock inference-profile ids (`us.anthropic.claude-haiku-4-5-...`) are not stable identifiers — hard-coding them would break on rotation.
4. The team uses the Civitas SDK on other projects, so reusing its helper saves us building one.

## Decision

- **Default provider in prod: Anthropic on Bedrock,** via `AsyncAnthropicBedrock`. Bearer token comes from `AWS_BEARER_TOKEN_BEDROCK` (see [keys.md](../../keys.md) for local values; Fly secrets in prod).
- **All Anthropic access goes through `civitas_core.llm.anthropic_client.build_anthropic_sdk_client()`.** It inspects `ANTHROPIC_PROVIDER`:
  - `=bedrock` → returns `AsyncAnthropicBedrock`.
  - unset/`=anthropic` → returns `AsyncAnthropic` (direct API, reads `ANTHROPIC_API_KEY`).
- **Logical model ids only in code.** Call `resolve_anthropic_model_id("claude-haiku-4-5")` to get the Bedrock inference-profile id at runtime. Never paste `us.anthropic.*` strings into application code.
- **Thin adapter layer.** `apps/api/app/agent/llm.py` wraps the Civitas client with our retry, length-validation, and seed-fallback behavior. Unit tests inject a fake adapter without touching env vars.

## Consequences

### Positive

- Same call sites work locally (direct) and in prod (Bedrock).
- Bearer rotation is a config change, not a code change.
- We get AWS-native observability (CloudWatch usage on the bearer token) for free.
- Prompt caching breakpoints work identically on both providers.

### Negative

- An extra dependency on `civitas_core` — but the team already maintains it; the cost is small.
- Bedrock has occasional regional throttling. Mitigation: we treat any LLM error as "fall back to the rendered template" (the seed is always valid).

### Neutral

- Bedrock pricing in `us-east-2` matches direct-API pricing closely; cost difference is within noise for our volume.

## Alternatives considered

- **Direct Anthropic API in prod.** Simpler, but conflicts with our AWS billing & access controls. Bedrock keeps everything inside our AWS account.
- **OpenAI / Azure OpenAI.** Considered; rejected because the agent narration is the *only* LLM call site, and we want consistency with our team's other Claude-based tooling.
- **Hard-code Bedrock profile ids per env.** Rejected — they rotate; the resolver helper is one fewer thing to remember.
- **Roll our own thin SDK.** Rejected — `civitas_core.llm.anthropic_client` already exists and is shared with sister projects.

## References

- [IMPLEMENTATION_PLAN.md §16.A](../../IMPLEMENTATION_PLAN.md)
- [keys.md](../../keys.md)
- [docs/runbooks/rotate-secrets.md](../runbooks/rotate-secrets.md)
- [ADR 0002: Deterministic agent](./0002-deterministic-agent.md)
