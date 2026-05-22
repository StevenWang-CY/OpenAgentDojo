import type { Metadata } from "next";
import { Badge } from "@/components/ui/Badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/Card";
import { env } from "@/lib/env";

export const metadata: Metadata = {
  title: "Status — OpenAgentDojo",
  description:
    "Real-time health of the OpenAgentDojo API, database, queue, and object storage.",
};

// Always render at request time — status is by definition not cacheable.
export const dynamic = "force-dynamic";
export const revalidate = 0;

// ---------------------------------------------------------------------------
// Backend contract — owned by the API agent.
//
// The current public endpoint lives at `GET ${API_BASE}/status` (root-mounted,
// no `/api/v1` prefix) and returns the documented shape below. This file is
// defensive about that contract:
//
//   - it falls back to `/api/v1/status` if the root path 404s, since the spec
//     for this work item lists `/api/v1/status` as the eventual location;
//   - it renders whatever subset of `components` the API returns, so the page
//     keeps working as new components are added (e.g. `sandbox_pool`, `workers`);
//   - on any 5xx / network error it shows a single "API offline" card instead
//     of throwing — the status page itself must never be a single point of
//     failure.
// ---------------------------------------------------------------------------

type ComponentStatus = "operational" | "degraded" | "down";

interface ComponentCheck {
  status: ComponentStatus;
  checked_at?: string;
  message?: string;
}

interface StatusPayload {
  status: ComponentStatus;
  components?: Record<string, ComponentCheck>;
  // Some backends emit `checks` instead of `components`; we accept either.
  checks?: Record<string, ComponentCheck | boolean>;
  version?: string;
  env?: string;
  uptime_seconds?: number;
  links?: Record<string, string>;
}

type FetchOutcome =
  | { kind: "ok"; payload: StatusPayload }
  | { kind: "offline"; reason: string };

async function fetchStatus(): Promise<FetchOutcome> {
  // Try the canonical path first, then fall back to the /api/v1 variant in
  // case the backend ever re-mounts the route there. We never throw — every
  // branch returns a typed FetchOutcome.
  const candidates = [`${env.apiBaseUrl}/status`, `${env.apiBaseUrl}/api/v1/status`];

  let lastReason = "no response";
  for (const url of candidates) {
    try {
      const res = await fetch(url, {
        cache: "no-store",
        headers: { Accept: "application/json" },
      });
      if (res.status === 404) {
        lastReason = `404 from ${url}`;
        continue; // try next candidate
      }
      if (res.status >= 500) {
        lastReason = `${res.status} from ${url}`;
        continue;
      }
      if (!res.ok) {
        lastReason = `HTTP ${res.status} from ${url}`;
        continue;
      }
      const payload = (await res.json()) as StatusPayload;
      return { kind: "ok", payload };
    } catch (err) {
      lastReason = err instanceof Error ? err.message : String(err);
    }
  }
  return { kind: "offline", reason: lastReason };
}

function normaliseComponents(payload: StatusPayload): Array<[string, ComponentCheck]> {
  // Prefer `components` (current backend contract). Fall back to `checks`
  // for forward-compat with the spec's eventual shape `{ db, redis, … }`.
  if (payload.components) {
    return Object.entries(payload.components);
  }
  if (payload.checks) {
    return Object.entries(payload.checks).map(([name, value]) => {
      if (typeof value === "boolean") {
        const status: ComponentStatus = value ? "operational" : "down";
        return [name, { status }];
      }
      return [name, value];
    });
  }
  return [];
}

const TONE_BY_STATUS: Record<ComponentStatus, "success" | "warning" | "danger"> = {
  operational: "success",
  degraded: "warning",
  down: "danger",
};

const LABEL_BY_STATUS: Record<ComponentStatus, string> = {
  operational: "Operational",
  degraded: "Degraded",
  down: "Down",
};

function componentLabel(name: string): string {
  // Render snake_case keys as human-readable titles.
  return name
    .split(/[_\s]+/)
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

function formatChecked(iso?: string): string | null {
  if (!iso) return null;
  try {
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return null;
    // SSR-safe: explicit UTC formatting, no Intl locale assumptions.
    return `${date.toISOString().replace("T", " ").replace(/\.\d+Z$/, "")} UTC`;
  } catch {
    return null;
  }
}

function formatUptime(seconds?: number): string | null {
  if (typeof seconds !== "number" || seconds < 0) return null;
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const mins = Math.floor((seconds % 3600) / 60);
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${mins}m`;
  if (mins > 0) return `${mins}m`;
  return `${seconds}s`;
}

export default async function StatusPage() {
  const outcome = await fetchStatus();
  const renderedAt = new Date();

  return (
    <section className="mx-auto w-full max-w-3xl px-6 py-16">
      <header className="mb-8 space-y-2">
        <h1 className="text-3xl font-semibold tracking-tight">System status</h1>
        <p className="text-sm text-[var(--color-muted-foreground)]">
          Live health of the OpenAgentDojo API and its dependencies. Refresh the page
          for a fresh probe — this page is never cached.
        </p>
      </header>

      {outcome.kind === "offline" ? (
        <Card aria-live="polite">
          <CardHeader className="flex flex-row items-start justify-between gap-4">
            <div>
              <CardTitle>API offline</CardTitle>
              <CardDescription>
                Could not reach the status endpoint at{" "}
                <code className="font-mono">{env.apiBaseUrl}</code>.
              </CardDescription>
            </div>
            <Badge tone="danger">Down</Badge>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-[var(--color-muted-foreground)]">
              {outcome.reason}
            </p>
          </CardContent>
        </Card>
      ) : (
        <OverallAndComponents
          payload={outcome.payload}
          renderedAt={renderedAt}
        />
      )}
    </section>
  );
}

function OverallAndComponents({
  payload,
  renderedAt,
}: {
  payload: StatusPayload;
  renderedAt: Date;
}) {
  const overall = (payload.status ?? "operational") as ComponentStatus;
  const components = normaliseComponents(payload);
  const uptime = formatUptime(payload.uptime_seconds);

  return (
    <>
      <Card className="mb-6" aria-live="polite">
        <CardHeader className="flex flex-row items-start justify-between gap-4">
          <div>
            <CardTitle>Overall</CardTitle>
            <CardDescription>
              {overall === "operational"
                ? "All systems normal."
                : overall === "degraded"
                  ? "One or more components are degraded — service may be slower or partially affected."
                  : "Major outage in progress."}
            </CardDescription>
          </div>
          <Badge tone={TONE_BY_STATUS[overall]}>{LABEL_BY_STATUS[overall]}</Badge>
        </CardHeader>
        <CardContent>
          <dl className="grid grid-cols-2 gap-3 text-sm text-[var(--color-muted-foreground)] sm:grid-cols-4">
            {payload.version && (
              <div>
                <dt className="uppercase tracking-wide text-[10px]">Version</dt>
                <dd className="font-mono text-[var(--color-foreground)]">
                  {payload.version}
                </dd>
              </div>
            )}
            {payload.env && (
              <div>
                <dt className="uppercase tracking-wide text-[10px]">Env</dt>
                <dd className="text-[var(--color-foreground)]">{payload.env}</dd>
              </div>
            )}
            {uptime && (
              <div>
                <dt className="uppercase tracking-wide text-[10px]">Uptime</dt>
                <dd className="text-[var(--color-foreground)]">{uptime}</dd>
              </div>
            )}
            <div>
              <dt className="uppercase tracking-wide text-[10px]">Checked</dt>
              <dd className="text-[var(--color-foreground)]">
                {renderedAt.toISOString().replace("T", " ").replace(/\.\d+Z$/, "")} UTC
              </dd>
            </div>
          </dl>
        </CardContent>
      </Card>

      <div className="grid gap-3">
        {components.length === 0 ? (
          <Card>
            <CardHeader>
              <CardTitle>No components reported</CardTitle>
              <CardDescription>
                The API is reachable but did not return any component checks.
              </CardDescription>
            </CardHeader>
          </Card>
        ) : (
          components.map(([name, check]) => {
            const status = (check.status ?? "operational") as ComponentStatus;
            const checked = formatChecked(check.checked_at);
            return (
              <Card key={name} aria-label={`Status: ${componentLabel(name)}`}>
                <CardHeader className="flex flex-row items-center justify-between gap-4">
                  <div>
                    <CardTitle className="text-sm">
                      {componentLabel(name)}
                    </CardTitle>
                    {checked && (
                      <CardDescription className="text-xs">
                        Last checked {checked}
                      </CardDescription>
                    )}
                  </div>
                  <Badge tone={TONE_BY_STATUS[status]}>
                    {LABEL_BY_STATUS[status]}
                  </Badge>
                </CardHeader>
                {check.message && (
                  <CardContent>
                    <p className="text-sm text-[var(--color-muted-foreground)]">
                      {check.message}
                    </p>
                  </CardContent>
                )}
              </Card>
            );
          })
        )}
      </div>

      {payload.links && Object.keys(payload.links).length > 0 && (
        <p className="mt-6 text-xs text-[var(--color-muted-foreground)]">
          Operator probes:{" "}
          {Object.entries(payload.links).map(([label, href], i, arr) => (
            <span key={label}>
              <a
                className="font-mono underline-offset-2 hover:underline"
                href={`${env.apiBaseUrl}${href}`}
              >
                {label}
              </a>
              {i < arr.length - 1 ? " · " : ""}
            </span>
          ))}
        </p>
      )}
    </>
  );
}
