/**
 * Centralised access to public env vars.
 * Use these instead of touching `process.env.NEXT_PUBLIC_*` directly so the
 * defaults stay in one place and missing-config bugs are caught at module load.
 */
export const env = {
  apiBaseUrl: trimTrailingSlash(
    process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000"
  ),
  wsBaseUrl: trimTrailingSlash(
    process.env.NEXT_PUBLIC_WS_BASE_URL ?? "ws://localhost:8000"
  ),
  appEnv: process.env.NEXT_PUBLIC_APP_ENV ?? "development",
  /** Canonical public URL of the deployed app — used by `metadataBase` (see app/layout.tsx). */
  appUrl: trimTrailingSlash(
    process.env.NEXT_PUBLIC_APP_URL ?? "http://localhost:3000"
  ),
  /** PostHog project API key. Empty string disables product analytics. */
  posthogKey: process.env.NEXT_PUBLIC_POSTHOG_KEY ?? "",
  /**
   * PostHog ingest host. No default — must be set explicitly per env so a
   * deploy that forgets to configure analytics cannot silently ship
   * telemetry to a third-party host without explicit operator intent.
   * Empty string disables product analytics (paired with posthogKey).
   */
  posthogHost: process.env.NEXT_PUBLIC_POSTHOG_HOST ?? "",
  /**
   * P0-7 — build-time override for the GitHub OAuth CTA on the sign-in
   * post-send card. The canonical signal is the runtime probe
   * ``auth.isGithubOAuthAvailable()``; this flag is a deliberate escape
   * hatch for preview builds that need to render the button before the
   * backend feature flag has rolled. ``true`` only when the env var is
   * literally ``"true"`` (case-insensitive); any other value disables
   * the override and the runtime probe wins.
   */
  githubOauthEnabledBuildtime:
    (process.env.NEXT_PUBLIC_GITHUB_OAUTH_ENABLED ?? "").toLowerCase() ===
    "true",
} as const;

function trimTrailingSlash(s: string): string {
  return s.endsWith("/") ? s.slice(0, -1) : s;
}
