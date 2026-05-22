import type { Metadata } from "next";
import { env } from "@/lib/env";
import { formatUtcDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";

export const metadata: Metadata = {
  title: "Status — OpenAgentDojo",
  description:
    "Real-time health of the OpenAgentDojo API, database, queue, and object storage.",
};

export const dynamic = "force-dynamic";
export const revalidate = 0;

type ComponentStatus = "operational" | "degraded" | "down";

interface ComponentCheck {
  status: ComponentStatus;
  checked_at?: string;
  message?: string;
}

interface StatusPayload {
  status: ComponentStatus;
  components?: Record<string, ComponentCheck>;
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
  const candidates = [
    `${env.apiBaseUrl}/status`,
    `${env.apiBaseUrl}/api/v1/status`,
  ];
  let lastReason = "no response";
  for (const url of candidates) {
    try {
      const res = await fetch(url, {
        cache: "no-store",
        headers: { Accept: "application/json" },
      });
      if (res.status === 404) {
        lastReason = `404 from ${url}`;
        continue;
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

function normaliseComponents(
  payload: StatusPayload,
): Array<[string, ComponentCheck]> {
  if (payload.components) return Object.entries(payload.components);
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

const ROW_KLASS: Record<ComponentStatus, string> = {
  operational: "",
  degraded: "is-degraded",
  down: "is-down",
};
const LAMP_KLASS: Record<ComponentStatus, string> = {
  operational: "bg-[var(--color-success)]",
  degraded: "bg-[var(--color-warning)]",
  down: "bg-[var(--color-danger)]",
};
const WORD_KLASS: Record<ComponentStatus, string> = {
  operational: "text-[var(--color-success)]",
  degraded: "text-[var(--color-warning)]",
  down: "text-[var(--color-danger)]",
};
const PULSE_KLASS = LAMP_KLASS;
const PULSE_RING: Record<ComponentStatus, string> = {
  operational:
    "shadow-[0_0_0_4px_oklch(from_var(--color-success)_l_c_h/0.18)]",
  degraded:
    "shadow-[0_0_0_4px_oklch(from_var(--color-warning)_l_c_h/0.18)]",
  down: "shadow-[0_0_0_4px_oklch(from_var(--color-danger)_l_c_h/0.18)]",
};
const OVERALL_HEADLINE: Record<ComponentStatus, string> = {
  operational: "All systems normal.",
  degraded: "One or more components are degraded.",
  down: "Major outage in progress.",
};
const OVERALL_WORD: Record<ComponentStatus, string> = {
  operational: "Operational",
  degraded: "Degraded",
  down: "Down",
};

function componentLabel(name: string): string {
  return name.toLowerCase().replace(/[\s]+/g, "_");
}

function componentDescription(name: string): string | null {
  const map: Record<string, string> = {
    api: "FastAPI · uvicorn",
    postgres: "primary + read replica",
    redis: "RQ broker + cache",
    sandbox_pool: "docker daemon · session containers",
    object_storage: "MinIO replay bucket",
    workers: "RQ workers · grading queue",
  };
  return map[name.toLowerCase()] ?? null;
}

function formatChecked(iso?: string): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) {
    const formatted = formatUtcDateTime(iso);
    return formatted === "—" ? null : formatted;
  }
  const hh = String(d.getUTCHours()).padStart(2, "0");
  const mm = String(d.getUTCMinutes()).padStart(2, "0");
  const ss = String(d.getUTCSeconds()).padStart(2, "0");
  return `${hh}:${mm}:${ss}`;
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
    <section className="mx-auto w-full max-w-3xl px-6 py-14">
      <p className="font-mono text-[11px] uppercase tracking-[0.18em] text-[var(--color-muted-foreground)]">
        <span className="text-[var(--color-primary)]">{"//"}</span> system
        status
      </p>
      <h1 className="mt-1.5 text-3xl font-semibold tracking-tight">
        {outcome.kind === "offline"
          ? "API offline."
          : OVERALL_HEADLINE[outcome.payload.status ?? "operational"]}
      </h1>
      <p className="mt-2.5 max-w-2xl text-[var(--color-muted-foreground)]">
        Live health of the OpenAgentDojo API and its dependencies. Refresh
        the page for a fresh probe — this page is never cached.
      </p>

      {outcome.kind === "offline" ? (
        <OfflineState reason={outcome.reason} baseUrl={env.apiBaseUrl} />
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
      <div
        aria-live="polite"
        className="mt-7 flex flex-col gap-5 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-6 py-5 sm:flex-row sm:items-center sm:justify-between sm:px-7"
      >
        <span className="inline-flex items-center gap-2.5 text-[17px] font-semibold tracking-tight">
          <span
            aria-hidden
            className={cn(
              "size-2.5 rounded-full",
              PULSE_KLASS[overall],
              PULSE_RING[overall],
            )}
          />
          {OVERALL_WORD[overall]}
        </span>
        <dl className="grid grid-cols-2 gap-6 font-mono sm:grid-cols-4 sm:gap-7">
          {payload.version ? (
            <Meta label="version" value={payload.version} />
          ) : null}
          {payload.env ? <Meta label="env" value={payload.env} /> : null}
          {uptime ? <Meta label="uptime" value={uptime} /> : null}
          <Meta label="checked" value={formatUtcDateTime(renderedAt)} />
        </dl>
      </div>

      <div className="mt-4 overflow-hidden rounded-lg border border-[var(--color-border)]">
        {components.length === 0 ? (
          <p className="bg-[var(--color-surface)] px-5 py-6 text-center font-mono text-xs text-[var(--color-muted-foreground)]">
            {"// no components reported."}
          </p>
        ) : (
          components.map(([name, check]) => {
            const status = (check.status ?? "operational") as ComponentStatus;
            const checked = formatChecked(check.checked_at);
            const desc = componentDescription(name);
            return (
              <div
                key={name}
                aria-label={`Status: ${componentLabel(name)}`}
                className={cn(
                  "grid items-center gap-4 border-b border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-3.5 last:border-b-0 sm:px-5",
                  ROW_KLASS[status],
                )}
                style={{
                  gridTemplateColumns:
                    "12px minmax(0,1fr) 140px minmax(0,120px)",
                }}
              >
                <span
                  aria-hidden
                  className={cn("size-2 rounded-full", LAMP_KLASS[status])}
                />
                <p className="truncate font-mono text-[13px] font-medium">
                  {componentLabel(name)}
                  {desc ? (
                    <span className="ml-2 font-normal text-[var(--color-muted-foreground)]">
                      {desc}
                    </span>
                  ) : null}
                </p>
                <p className="font-mono text-[11px] text-[var(--color-muted-foreground)]">
                  {checked ?? "—"}
                </p>
                <p
                  className={cn(
                    "text-right font-mono text-[11px] uppercase tracking-[0.08em]",
                    WORD_KLASS[status],
                  )}
                >
                  {status}
                </p>
                {check.message ? (
                  <p
                    className="col-span-full pl-7 font-mono text-[11px] text-[var(--color-muted-foreground)]"
                    style={{ gridColumn: "1 / -1" }}
                  >
                    {check.message}
                  </p>
                ) : null}
              </div>
            );
          })
        )}
      </div>

      {payload.links && Object.keys(payload.links).length > 0 ? (
        <p className="mt-5 font-mono text-[11px] text-[var(--color-muted-foreground)]">
          operator probes:{" "}
          {Object.entries(payload.links).map(([label, href], i, arr) => (
            <span key={label}>
              <a
                className="underline underline-offset-2 hover:text-[var(--color-foreground)]"
                href={`${env.apiBaseUrl}${href}`}
              >
                {label}
              </a>
              {i < arr.length - 1 ? " · " : ""}
            </span>
          ))}
        </p>
      ) : null}
    </>
  );
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="font-mono text-[10.5px] uppercase tracking-[0.08em] text-[var(--color-muted-foreground)]">
        {label}
      </dt>
      <dd className="mt-0.5 font-mono text-[13px]">{value}</dd>
    </div>
  );
}

function OfflineState({
  reason,
  baseUrl,
}: {
  reason: string;
  baseUrl: string;
}) {
  return (
    <div
      aria-live="polite"
      className="mt-7 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-6 py-5"
    >
      <span className="inline-flex items-center gap-2.5 text-[17px] font-semibold tracking-tight">
        <span
          aria-hidden
          className={cn(
            "size-2.5 rounded-full",
            PULSE_KLASS.down,
            PULSE_RING.down,
          )}
        />
        Down
      </span>
      <p className="mt-2 text-sm text-[var(--color-muted-foreground)]">
        Could not reach the status endpoint at{" "}
        <code className="font-mono">{baseUrl}</code>.
      </p>
      <p className="mt-1.5 font-mono text-[11px] text-[var(--color-muted-foreground)]">
        {reason}
      </p>
    </div>
  );
}
