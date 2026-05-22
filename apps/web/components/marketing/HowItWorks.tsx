/**
 * Four artifact-grounded steps. Each step shows the actual thing it
 * produces (a brief, a real diff, a terminal log, a score readout) rather
 * than an icon-in-a-card. The eyebrows, numbers, kv lists, and artifact
 * chrome all lean on the mono token so the page reads as developer-tool.
 */
export function HowItWorks() {
  return (
    <section
      aria-labelledby="how-heading"
      className="border-b border-[var(--color-border)]"
    >
      <div className="mx-auto max-w-6xl px-6 py-24">
        <p className="font-mono text-[11px] uppercase tracking-[0.18em] text-[var(--color-muted-foreground)]">
          // how it works
        </p>
        <h2
          id="how-heading"
          className="mt-2 max-w-[700px] text-3xl font-semibold tracking-tight"
        >
          Four steps. Each one feeds the score.
        </h2>
        <p className="mt-3 max-w-[620px] text-[var(--color-muted-foreground)]">
          The platform watches how you supervise &mdash; not just whether the
          bug goes away. Every prompt, diff, command, and edit streams into
          an append-only supervision log that the grader replays
          deterministically.
        </p>

        <ol className="mt-12 grid gap-8">
          <Step
            num="01 / pick"
            title="Pick a mission."
            body="Each mission is a real repo with a real bug and a frozen, deterministic agent patch ready to apply."
            kv={[
              ["repo", "fullstack-auth-demo@v1"],
              ["brief", "session cookie validation"],
              ["fail", "hidden, on submit"],
            ]}
            artifact={<BriefArtifact />}
          />
          <Step
            num="02 / review"
            title="Read the agent’s patch with skepticism."
            body="The diff looks reasonable. It isn’t. The grader watches whether you opened it, scrolled it, and pushed back on what looks plausibly wrong."
            kv={[
              ["event", "diff.opened · 1×"],
              ["event", "agent.prompt · 1× corrective"],
              ["watch", "line 47 — presence-only check"],
            ]}
            artifact={<DiffArtifact />}
          />
          <Step
            num="03 / verify"
            title="Correct and verify in-sandbox."
            body="Edit files, add regression tests, rerun your checks. Every command, every keystroke goes on the timeline — that’s what the verification score grades."
            kv={[
              ["tests", "4/4 visible passing"],
              ["tools", "pytest · mypy · ruff"],
              ["events", "command.run × 6"],
            ]}
            artifact={<TerminalArtifact />}
          />
          <Step
            num="04 / grade"
            title="Get graded on the process."
            body="Hidden tests + structural validators score seven supervision dimensions deterministically. No LLM on the grading path, ever. Replays are byte-identical."
            kv={[
              ["total", "78 / 100"],
              ["badge", "regression-test-writer"],
              ["repro", "replay id 7c41…f9"],
            ]}
            artifact={<ScoreArtifact />}
          />
        </ol>
      </div>
    </section>
  );
}

type StepProps = {
  num: string;
  title: string;
  body: string;
  kv: ReadonlyArray<readonly [string, string]>;
  artifact: React.ReactNode;
};

function Step({ num, title, body, kv, artifact }: StepProps) {
  return (
    <li className="grid grid-cols-1 gap-8 border-t border-[var(--color-border)] pt-8 first:border-t-0 first:pt-0 lg:grid-cols-[64px_1fr_minmax(420px,520px)] lg:items-start">
      <div className="font-mono text-[13px] tracking-[0.06em] text-[var(--color-muted-foreground)]">
        {num}
      </div>
      <div>
        <h3 className="text-xl font-semibold tracking-tight">{title}</h3>
        <p className="mt-2 max-w-[38ch] text-pretty text-[var(--color-muted-foreground)]">
          {body}
        </p>
        <dl className="mt-4 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 font-mono text-xs text-[var(--color-muted-foreground)]">
          {kv.map(([k, v]) => (
            <div key={`${k}-${v}`} className="contents">
              <dt>{k}</dt>
              <dd className="font-medium text-[var(--color-foreground)]">{v}</dd>
            </div>
          ))}
        </dl>
      </div>
      <div className="min-w-0">{artifact}</div>
    </li>
  );
}

/* ── Artifact cards ──────────────────────────────────────────────────── */

function ArtifactShell({
  path,
  children,
  className,
  headerExtra,
}: {
  path: string;
  children: React.ReactNode;
  className?: string;
  headerExtra?: React.ReactNode;
}) {
  return (
    <div
      className={
        "overflow-hidden rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] font-mono text-[12.5px] leading-relaxed shadow-soft " +
        (className ?? "")
      }
    >
      <div className="flex items-center gap-2 border-b border-[var(--color-border)] bg-[var(--color-surface-elevated)] px-3 py-2 text-[11px] text-[var(--color-muted-foreground)]">
        <span className="font-medium text-[var(--color-foreground)]">{path}</span>
        {headerExtra}
      </div>
      <div className="px-3.5 py-3">{children}</div>
    </div>
  );
}

function BriefArtifact() {
  return (
    <ArtifactShell path="missions/01-auth-cookie-expiration/brief.md">
      <div className="flex flex-wrap gap-1.5">
        {["AUTH", "INTERMEDIATE", "35 min"].map((tag) => (
          <span
            key={tag}
            className="rounded border border-[var(--color-border-strong,var(--color-border))] px-1.5 py-0.5 text-[10.5px] text-[var(--color-muted-foreground)]"
          >
            {tag}
          </span>
        ))}
      </div>
      <p className="mt-2.5 font-sans text-[15px] font-semibold text-[var(--color-foreground)]">
        Expired Session Cookie Still Grants Access
      </p>
      <p className="mt-1.5 font-sans text-[13px] text-[var(--color-muted-foreground)]">
        Users with expired session cookies can still access protected routes.
        Reports from QA say the cookie check &ldquo;runs&rdquo; but the dashboard
        renders anyway.
      </p>
      <p className="mt-3.5 text-[11px] text-[var(--color-muted-foreground)]">
        failure_mode &nbsp;·&nbsp;{" "}
        <b className="font-medium text-[var(--color-warning)]">
          checks_presence_not_expiration
        </b>
      </p>
    </ArtifactShell>
  );
}

function DiffArtifact() {
  const lines: ReadonlyArray<{
    n: string;
    tone: "ctx" | "del" | "add";
    code: string;
  }> = [
    { n: "44", tone: "ctx", code: "  def validate(cookie: str) -> bool:" },
    { n: "45", tone: "del", code: "  raw = decode(cookie)" },
    { n: "45", tone: "add", code: '  raw = decode(cookie or "")' },
    { n: "46", tone: "del", code: '  return raw["uid"] in active_uids' },
    { n: "47", tone: "add", code: "  if not raw: return False" },
    { n: "48", tone: "add", code: '  return raw["uid"] in active_uids' },
  ];
  return (
    <ArtifactShell
      path="apps/api/app/auth/session.py"
      headerExtra={<span className="ml-auto opacity-60">diff</span>}
    >
      <div className="-mx-3.5">
        {lines.map((line, idx) => (
          <div
            key={`${line.n}-${idx}`}
            className={
              "grid grid-cols-[28px_1fr] items-baseline px-0 " +
              (line.tone === "add"
                ? "bg-[oklch(from_var(--color-success)_l_c_h/0.10)]"
                : line.tone === "del"
                  ? "bg-[oklch(from_var(--color-danger)_l_c_h/0.10)]"
                  : "")
            }
          >
            <span
              className={
                "pr-2.5 text-right text-[11px] text-[var(--color-muted-foreground)] before:font-semibold " +
                (line.tone === "add"
                  ? "before:content-['+_'] before:text-[var(--color-success)]"
                  : line.tone === "del"
                    ? "before:content-['−_'] before:text-[var(--color-danger)]"
                    : "")
              }
            >
              {line.n}
            </span>
            <span className="whitespace-pre pr-3">{line.code}</span>
          </div>
        ))}
      </div>
      <div className="mt-3 border-l-2 border-[var(--color-warning)] bg-[oklch(from_var(--color-warning)_l_c_h/0.12)] px-2.5 py-1.5 font-sans text-xs text-[var(--color-warning)]">
        ⚠ guard added against a missing cookie &mdash; but{" "}
        <code className="font-mono">raw[&quot;exp&quot;]</code> is still never
        checked.
      </div>
    </ArtifactShell>
  );
}

function TerminalArtifact() {
  // Dark terminal panel — fixed colors (not theme-driven) because a terminal
  // is the same dark surface in every product, regardless of app theme.
  return (
    <div className="overflow-hidden rounded-lg border border-[oklch(30%_0.02_252)] bg-[oklch(18%_0.02_252)] font-mono text-[12.5px] leading-relaxed text-[oklch(96%_0.005_240)] shadow-soft">
      <div className="border-b border-[oklch(30%_0.02_252)] bg-[oklch(21%_0.02_252)] px-3 py-2 text-[11px] text-[oklch(70%_0.015_250)]">
        <span className="font-medium text-[oklch(96%_0.005_240)]">
          sandbox · fullstack-auth-demo
        </span>
      </div>
      <div className="space-y-0.5 px-3.5 py-3">
        <Line>
          <Prompt /> pytest tests/auth -q
        </Line>
        <Line dim>
          tests/auth/test_session.py ...... <Ok>[ 6 passed ]</Ok>
        </Line>
        <Line dim>
          tests/auth/test_refresh.py F. <Bad>[ 1 failed ]</Bad>
        </Line>
        <Line>&nbsp;</Line>
        <Line dim>&gt; assert validate(expired_cookie) is False</Line>
        <Line>
          <Bad>E&nbsp;&nbsp;assert True is False</Bad>
        </Line>
        <Line>&nbsp;</Line>
        <Line>
          <Prompt /> mypy apps/api/app/auth
        </Line>
        <Line dim>Success: no issues found in 4 source files</Line>
        <Line>
          <Prompt />{" "}
          <span className="text-[oklch(78%_0.13_200)]">dojo</span> submit
          --note &quot;Added expiry check + regression test&quot;
        </Line>
      </div>
    </div>
  );
}

function Line({ children, dim = false }: { children: React.ReactNode; dim?: boolean }) {
  return (
    <span className={"block " + (dim ? "text-[oklch(70%_0.015_250)]" : "")}>
      {children}
    </span>
  );
}
function Prompt() {
  return <span className="text-[var(--color-primary)]">$</span>;
}
function Ok({ children }: { children: React.ReactNode }) {
  return <span className="text-[var(--color-success)]">{children}</span>;
}
function Bad({ children }: { children: React.ReactNode }) {
  return <span className="text-[var(--color-danger)]">{children}</span>;
}

function ScoreArtifact() {
  const rows: ReadonlyArray<{ nm: string; pts: number; max: number }> = [
    { nm: "Final patch correctness", pts: 24, max: 30 },
    { nm: "Verification discipline", pts: 11, max: 15 },
    { nm: "Agent output review", pts: 11, max: 15 },
    { nm: "Prompt quality", pts: 7, max: 10 },
  ];
  return (
    <ArtifactShell
      path="report.score"
      headerExtra={<span className="ml-auto font-mono">78 / 100</span>}
    >
      <div className="grid gap-2">
        {rows.map((row) => {
          const pct = Math.round((row.pts / row.max) * 100);
          return (
            <div
              key={row.nm}
              className="grid grid-cols-[1fr_60px] items-center gap-3 border-t border-[var(--color-border)] py-1.5 first:border-t-0"
            >
              <div className="font-sans text-xs text-[var(--color-foreground)]">
                {row.nm}
              </div>
              <div className="text-right text-xs text-[var(--color-muted-foreground)]">
                <b className="font-semibold text-[var(--color-foreground)]">
                  {row.pts}
                </b>
                {" "}/{row.max}
              </div>
              <div className="col-span-2 mt-0.5 h-1 overflow-hidden rounded-sm bg-[var(--color-muted)]">
                <div
                  className="h-full bg-[var(--color-foreground)]"
                  style={{ width: `${pct}%` }}
                />
              </div>
            </div>
          );
        })}
      </div>
    </ArtifactShell>
  );
}
