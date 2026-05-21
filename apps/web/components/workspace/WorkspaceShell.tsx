"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, Loader2, RefreshCcw, Send, WifiOff } from "lucide-react";
import { toast } from "sonner";
import type {
  AgentTurn,
  SessionStatus,
  SupervisionEvent,
} from "@arena/shared-types";
import {
  ApiError,
  applyPatch,
  getDiff,
  getFileTree,
  getSession,
  getSubmission,
  getTimeline,
  markDiffOpened,
  submitPrompt,
} from "@/lib/api";
import {
  createReconnectingSocket,
  type ReconnectingSocketStatus,
} from "@/lib/ws";
import { useWorkspaceStore } from "@/stores/workspaceStore";
import { PanelLayout } from "./PanelLayout";
import { FileTree } from "./FileTree";
import { ContextSelector } from "./ContextSelector";
import { CodeEditor } from "./CodeEditor";
import { DiffViewer } from "./DiffViewer";
import { Terminal } from "./Terminal";
import { TestPanel } from "./TestPanel";
import { AgentChat } from "./AgentChat";
import { Timeline } from "./Timeline";
import { MissionBrief } from "./MissionBrief";
import { ScorePreview } from "./ScorePreview";
import { WorkspaceTopBar } from "./WorkspaceTopBar";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/Tabs";
import { Button } from "@/components/ui/Button";

interface WorkspaceShellProps {
  sessionId: string;
}

type WorkspaceTab = "editor" | "diff";

/**
 * Top-level workspace orchestrator. Owns:
 *   - the React Query bindings (session, tree, diff, submission lookup)
 *   - the live supervision-event WebSocket
 *   - the agent-turn / apply-patch wiring
 *   - status-driven branching (provisioning, active, submitting, graded, abandoned, error)
 *
 * File reads + writes are pushed down to `CodeEditor`; this shell just
 * resolves the active path + delegates.
 */
export function WorkspaceShell({ sessionId }: WorkspaceShellProps) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const store = useWorkspaceStore(sessionId);

  // Granular selectors — we only re-render this shell on changes to the
  // specific fields we actually use, not on every fileBuffer keystroke.
  const selectedContext = store((s) => s.selectedContext);
  const activeFile = store((s) => s.activeFile);
  const sandboxDriver = store((s) => s.sandboxDriver);
  const events = store((s) => s.events);
  const agentTurns = store((s) => s.agentTurns);
  // Action references are stable across renders (Zustand returns the same
  // function refs), so picking them individually is a one-time cost.
  const pushEvent = store((s) => s.pushEvent);
  const pushAgentTurn = store((s) => s.pushAgentTurn);
  const setSandboxDriver = store((s) => s.setSandboxDriver);
  const openFile = store((s) => s.openFile);
  const toggleContextPath = store((s) => s.toggleContextPath);
  const setSelectedContext = store((s) => s.setSelectedContext);

  // Controlled tab state — `openFile` flips us to the editor tab so the
  // user lands on the file they just clicked instead of an old surface.
  const [tab, setTab] = React.useState<WorkspaceTab>("editor");

  // Live WS connection state for the events stream — drives the unobtrusive
  // banner that tells the user "reconnecting…" so transient drops don't look
  // like the app froze. Defaults to "open" so we don't flash a banner during
  // the brief window before the first connect.
  const [wsStatus, setWsStatus] = React.useState<ReconnectingSocketStatus>("open");

  // Ref tracks the latest event id we've ingested. The WS reconnect logic
  // reads this synchronously on every connect so the close-and-reopen path
  // picks up the freshest value (vs. a value captured at effect mount).
  const lastEventIdRef = React.useRef(0);
  React.useEffect(() => {
    const last = events.length === 0 ? 0 : events[events.length - 1]!.id;
    if (last > lastEventIdRef.current) lastEventIdRef.current = last;
  }, [events]);

  const sessionQuery = useQuery({
    queryKey: ["session", sessionId],
    queryFn: ({ signal }) => getSession(sessionId, signal),
    refetchInterval: (q) => {
      const data = q.state.data;
      if (!data) return 2_000;
      // Poll only while a transition is in flight.
      return data.status === "provisioning" || data.status === "submitting"
        ? 2_000
        : false;
    },
  });

  const status: SessionStatus | undefined = sessionQuery.data?.status;

  const treeQuery = useQuery({
    queryKey: ["session", sessionId, "tree"],
    queryFn: ({ signal }) => getFileTree(sessionId, signal),
    enabled: status === "active",
  });

  const diffQuery = useQuery({
    queryKey: ["session", sessionId, "diff"],
    queryFn: ({ signal }) => getDiff(sessionId, signal),
    enabled: status === "active",
    refetchInterval: 5_000,
  });

  // Initial timeline backfill — runs once on mount when we have a session.
  // After that, the live WS stream is the source of truth, so this query is
  // explicitly *not* polled on an interval per spec.
  const timelineQuery = useQuery({
    queryKey: ["session", sessionId, "timeline"],
    queryFn: ({ signal }) => getTimeline(sessionId, signal),
    enabled: !!sessionQuery.data,
    staleTime: Infinity,
    refetchOnMount: false,
    refetchOnWindowFocus: false,
  });

  // Push backfilled events into the store exactly once after they arrive.
  // The pushEvent action ref is stable, so depending on it here is safe.
  const backfilledRef = React.useRef(false);
  const backfillEvents = timelineQuery.data;
  React.useEffect(() => {
    if (backfilledRef.current) return;
    if (!backfillEvents || backfillEvents.length === 0) return;
    backfilledRef.current = true;
    for (const e of backfillEvents) pushEvent(e);
  }, [backfillEvents, pushEvent]);

  // Surface a session-errored event as a one-shot banner.
  const erroredBannerRef = React.useRef<number | null>(null);
  React.useEffect(() => {
    const errored = events.find((e) => e.event_type === "session.errored");
    if (!errored) return;
    if (erroredBannerRef.current === errored.id) return;
    erroredBannerRef.current = errored.id;
    const payload = errored.payload as { stage?: string; detail?: string };
    toast.error(
      `Session ${payload.stage ?? "error"}: ${payload.detail ?? "unknown failure"}`
    );
  }, [events]);

  // ── Live events WebSocket ────────────────────────────────────────────────
  const wsToken = sessionQuery.data?.ws_token;
  const sessionLoaded = !!sessionQuery.data;
  React.useEffect(() => {
    if (!sessionLoaded) return;
    if (status !== "active" && status !== "submitting") return;

    const socket = createReconnectingSocket({
      url: `/ws/sessions/${sessionId}/events`,
      token: wsToken,
      sessionId,
      // Resolved synchronously on every (re)connect, so reconnects pick up
      // the freshest `lastEventId` from the ref — no need for the effect
      // to re-mount on every event.
      resolveQueryParams: () =>
        lastEventIdRef.current > 0
          ? { last_id: lastEventIdRef.current }
          : {},
      onMessage(ev) {
        if (typeof ev.data !== "string") return;
        try {
          const parsed = JSON.parse(ev.data) as unknown;
          if (!isSupervisionEvent(parsed)) return;
          pushEvent(parsed);
          if (parsed.event_type === "agent.responded") {
            const turn = synthesiseAgentTurn(parsed);
            if (turn) pushAgentTurn(turn);
          }
          if (parsed.event_type === "patch.applied") {
            queryClient.invalidateQueries({
              queryKey: ["session", sessionId, "tree"],
            });
            queryClient.invalidateQueries({
              queryKey: ["session", sessionId, "diff"],
            });
          }
          if (parsed.event_type === "submission.failed") {
            const payload = parsed.payload as { stage?: string; detail?: string };
            toast.error(
              `Submission failed at ${payload.stage ?? "grading"}: ${payload.detail ?? "unknown error"}`
            );
          }
          // patch.proposed and patch.failed accumulate in the events array
          // via pushEvent above; the Timeline renders them, and no extra
          // store/UI hooks are required at the shell level.
        } catch {
          // ignore malformed frames
        }
      },
      onStatusChange(next) {
        setWsStatus(next);
      },
      onAttemptsExhausted() {
        toast.error(
          "Lost connection to the session event stream. Refresh the page to reconnect."
        );
      },
    });

    return () => {
      socket.close();
    };
  }, [
    sessionId,
    status,
    wsToken,
    sessionLoaded,
    pushEvent,
    pushAgentTurn,
    queryClient,
  ]);

  // Sync sandbox driver into the store so the topbar banner can pick it up.
  const sandboxDriverFromApi = sessionQuery.data?.sandbox_driver;
  React.useEffect(() => {
    if (sandboxDriverFromApi) {
      setSandboxDriver(sandboxDriverFromApi);
    }
  }, [sandboxDriverFromApi, setSandboxDriver]);

  // (Debounced context sync lives inside <FileTree/> so that each toggle
  //  collapses cleanly to one POST without the shell having to know.)

  // ── Status branches ──────────────────────────────────────────────────────

  // A `submission.failed` WS event is authoritative for surfacing failures
  // even when the polled session status hasn't flipped yet.
  const submissionFailed = React.useMemo(
    () => events.some((e) => e.event_type === "submission.failed"),
    [events]
  );

  // When the session flips to `graded`, look up the submission so we can
  // redirect to /report/{submission.id}.
  const submissionQuery = useQuery({
    queryKey: ["session", sessionId, "submission"],
    queryFn: ({ signal }) => getSubmission(sessionId, signal),
    enabled: status === "graded",
    retry: false,
  });

  React.useEffect(() => {
    if (status === "graded" && submissionQuery.data) {
      router.replace(`/report/${submissionQuery.data.id}`);
    }
  }, [status, submissionQuery.data, router]);

  // ── Top-level loading / error gates ──────────────────────────────────────

  if (sessionQuery.isLoading) {
    return <LoadingShell />;
  }

  if (sessionQuery.error) {
    const apiError =
      sessionQuery.error instanceof ApiError ? sessionQuery.error : null;
    return (
      <FullPageMessage
        tone="danger"
        icon={<AlertCircle aria-hidden className="size-6" />}
        heading="We couldn't open this session."
        body={
          apiError?.status === 0
            ? "Couldn't reach the API. Is the backend running on port 8000?"
            : (apiError?.message ?? "Unexpected error.")
        }
        actions={
          <>
            <Button
              variant="secondary"
              onClick={() => void sessionQuery.refetch()}
            >
              <RefreshCcw className="size-4" aria-hidden /> Retry
            </Button>
            <Button asChild variant="ghost">
              <Link href="/missions">Back to missions</Link>
            </Button>
          </>
        }
      />
    );
  }

  const session = sessionQuery.data;
  if (!session) return null;

  if (status === "provisioning") {
    return (
      <FullPageMessage
        tone="info"
        icon={<Loader2 aria-hidden className="size-6 animate-spin" />}
        heading="Provisioning your sandbox…"
        body="Cold start usually takes 10–25 seconds."
      />
    );
  }

  if (status === "submitting" && !submissionFailed) {
    return <GradingWait sessionId={sessionId} />;
  }

  if (status === "graded") {
    return (
      <FullPageMessage
        tone="info"
        icon={<Loader2 aria-hidden className="size-6 animate-spin" />}
        heading="Submission graded."
        body="Taking you to your report…"
        actions={
          submissionQuery.data ? (
            <Button asChild>
              <Link href={`/report/${submissionQuery.data.id}`}>
                Open report
              </Link>
            </Button>
          ) : null
        }
      />
    );
  }

  if (status === "abandoned") {
    return (
      <FullPageMessage
        tone="warning"
        icon={<AlertCircle aria-hidden className="size-6" />}
        heading="This session was abandoned."
        body="The sandbox has been torn down. You can start a fresh attempt at this mission whenever you're ready."
        actions={
          <Button asChild>
            <Link href={`/missions/${session.mission_id}`}>
              <Send className="size-4" aria-hidden /> Restart mission
            </Link>
          </Button>
        }
      />
    );
  }

  if (status === "error" || submissionFailed) {
    const failedEvent = events.find(
      (e) => e.event_type === "submission.failed"
    );
    const failedPayload = failedEvent?.payload as
      | { stage?: string; detail?: string }
      | undefined;
    const body = failedPayload
      ? `${failedPayload.stage ? `${failedPayload.stage}: ` : ""}${failedPayload.detail ?? "Unknown grading error."}`
      : "Sandbox provisioning failed. Start a fresh attempt — no progress is lost since the session never went active.";
    return (
      <FullPageMessage
        tone="danger"
        icon={<AlertCircle aria-hidden className="size-6" />}
        heading="Something went wrong with this session."
        body={body}
        actions={
          <>
            <Button asChild>
              <Link href={`/missions/${session.mission_id}`}>Restart mission</Link>
            </Button>
            <Button asChild variant="ghost">
              <Link href="/missions">Back to missions</Link>
            </Button>
          </>
        }
      />
    );
  }

  const mission = session.mission;
  const tree = treeQuery.data ?? [];
  const diff = diffQuery.data?.unified_diff ?? "";

  return (
    <div className="flex flex-col">
      <WorkspaceTopBar
        sessionId={sessionId}
        missionId={mission.id}
        missionTitle={mission.title}
        difficulty={mission.difficulty}
        sandboxDriver={sandboxDriver}
        events={events}
        selectedContext={selectedContext}
        expectedRequiredContext={mission.expected_context_required}
        diffChangedFiles={diff ? extractChangedFiles(diff) : []}
        onSubmitted={(submissionId) => router.push(`/report/${submissionId}`)}
      />

      <WsStatusBanner status={wsStatus} />

      <PanelLayout
        missionId={mission.id}
        sidebar={
          <div className="flex h-full flex-col">
            <header className="flex items-center justify-between border-b border-[var(--color-border)] bg-[var(--color-surface-elevated)] px-3 py-1.5">
              <p className="text-[10px] font-semibold uppercase tracking-wide text-[var(--color-muted-foreground)]">
                Files
              </p>
            </header>
            <div className="flex-1 min-h-0">
              <FileTree
                nodes={tree}
                sessionId={sessionId}
                activePath={activeFile}
                selectedContext={selectedContext}
                onOpenFile={(p) => {
                  openFile(p);
                  setTab("editor");
                }}
                onToggleContext={(p) => toggleContextPath(p)}
              />
            </div>
            <div className="border-t border-[var(--color-border)] bg-[var(--color-surface-elevated)] p-3">
              <ContextSelector
                selected={selectedContext}
                onRemove={(p) => toggleContextPath(p)}
                onClear={() => setSelectedContext([])}
              />
            </div>
          </div>
        }
        editor={
          <Tabs
            value={tab}
            onValueChange={(v) => setTab(v as WorkspaceTab)}
            className="flex h-full flex-col"
          >
            <div className="flex items-center justify-between border-b border-[var(--color-border)] bg-[var(--color-surface-elevated)] px-2 py-1.5">
              <TabsList>
                <TabsTrigger value="editor">Editor</TabsTrigger>
                <TabsTrigger value="diff">Diff</TabsTrigger>
              </TabsList>
              <p className="font-mono text-[11px] text-[var(--color-muted-foreground)]">
                {activeFile ?? "no file open"}
              </p>
            </div>
            <TabsContent value="editor" className="mt-0 flex-1 min-h-0">
              <CodeEditor sessionId={sessionId} path={activeFile} />
            </TabsContent>
            <TabsContent value="diff" className="mt-0 flex-1 min-h-0">
              <DiffViewer
                unifiedDiff={diff}
                activePath={activeFile ?? ""}
                onDiffOpened={(path) => {
                  void markDiffOpened(sessionId, path).catch(() => {
                    /* best-effort — scoring degrades gracefully */
                  });
                }}
              />
            </TabsContent>
          </Tabs>
        }
        rightTabs={[
          {
            id: "brief",
            label: "Brief",
            content: (
              <MissionBrief brief={mission.brief} title={mission.title} />
            ),
          },
          {
            id: "signals",
            label: "Signals",
            content: (
              <div className="overflow-auto p-3">
                <ScorePreview
                  expectedRequiredContext={mission.expected_context_required}
                  selectedContext={selectedContext}
                  events={events}
                  changedFiles={diff ? extractChangedFiles(diff) : []}
                />
              </div>
            ),
          },
          {
            id: "chat",
            label: "Agent",
            content: (
              <AgentChat
                turns={agentTurns}
                contextPaths={selectedContext}
                onSubmit={(text) =>
                  handleAgentSubmit(
                    sessionId,
                    selectedContext,
                    pushAgentTurn,
                    text
                  )
                }
                onApplyPatch={(turnId) =>
                  handleApplyPatch(queryClient, sessionId, turnId)
                }
              />
            ),
          },
        ]}
        bottomTabs={[
          {
            id: "terminal",
            label: "Terminal",
            content: <Terminal sessionId={sessionId} token={wsToken} />,
          },
          {
            id: "tests",
            label: "Tests",
            content: <TestPanel sessionId={sessionId} />,
          },
          {
            id: "timeline",
            label: "Timeline",
            content: <Timeline events={events} />,
          },
        ]}
      />
    </div>
  );
}

// ── Helpers ──────────────────────────────────────────────────────────────────

async function handleAgentSubmit(
  sessionId: string,
  selectedContext: string[],
  pushAgentTurn: (turn: AgentTurn) => void,
  text: string
): Promise<void> {
  try {
    const turn = await submitPrompt(sessionId, {
      text,
      context: {
        files: selectedContext,
        logs: [],
        tests: [],
        extras: [],
      },
    });
    pushAgentTurn(turn);
  } catch (err) {
    toast.error(
      err instanceof ApiError ? err.message : "Failed to send prompt."
    );
  }
}

async function handleApplyPatch(
  queryClient: ReturnType<typeof useQueryClient>,
  sessionId: string,
  turnId: string
): Promise<void> {
  try {
    const result = await applyPatch(sessionId, turnId);
    if (!result.applied) {
      toast.error(result.error ?? "Patch could not be applied.");
      return;
    }
    toast.success(
      `Patch applied to ${result.files_changed.length} file${result.files_changed.length === 1 ? "" : "s"}.`
    );
    queryClient.invalidateQueries({ queryKey: ["session", sessionId, "tree"] });
    queryClient.invalidateQueries({ queryKey: ["session", sessionId, "diff"] });
  } catch (err) {
    toast.error(
      err instanceof ApiError ? err.message : "Failed to apply patch."
    );
  }
}

function isSupervisionEvent(value: unknown): value is SupervisionEvent {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v.event_type === "string" &&
    typeof v.occurred_at === "string" &&
    typeof v.payload === "object" &&
    v.payload !== null
  );
}

/**
 * The `agent.responded` event has a `response_summary` — enough to display a
 * placeholder turn while the REST POST resolves. Once the actual `AgentTurn`
 * arrives via `submitPrompt` the store dedupes by `turn_index`.
 */
function synthesiseAgentTurn(event: SupervisionEvent): AgentTurn | null {
  if (event.event_type !== "agent.responded") return null;
  const payload = event.payload;
  return {
    id: `synthetic-${event.id}`,
    session_id: event.session_id,
    turn_index: payload.turn_index,
    user_prompt: "",
    selected_context: { files: [], logs: [], tests: [], extras: [] },
    agent_response: payload.response_summary,
    proposed_actions: [],
    applied_patch: null,
    patch_applied_at: null,
    created_at: event.occurred_at,
  };
}

/** Pull the changed paths out of a unified diff for the ScorePreview hint. */
function extractChangedFiles(diff: string): string[] {
  const paths: string[] = [];
  for (const line of diff.split("\n")) {
    if (line.startsWith("+++ b/")) paths.push(line.slice(6).trim());
    else if (line.startsWith("+++ ")) paths.push(line.slice(4).trim());
  }
  return Array.from(new Set(paths.filter((p) => p && p !== "/dev/null")));
}

// ── Presentational sub-components ───────────────────────────────────────────

function LoadingShell() {
  return (
    <div className="flex h-[calc(100dvh-3.5rem)] items-center justify-center text-sm text-[var(--color-muted-foreground)]">
      <Loader2 className="mr-2 size-4 animate-spin" aria-hidden /> Loading session…
    </div>
  );
}

/**
 * In-flow banner for transient WS drops. Stays out of the way during normal
 * operation; appears the moment the events stream goes into backoff so users
 * understand events are temporarily paused (not lost). `exhausted` flips it
 * red so they know a refresh is needed.
 */
function WsStatusBanner({ status }: { status: ReconnectingSocketStatus }) {
  if (status !== "reconnecting" && status !== "exhausted") return null;
  const isExhausted = status === "exhausted";
  return (
    <div
      role="status"
      aria-live="polite"
      className={
        isExhausted
          ? "flex items-center gap-2 border-b border-[var(--color-danger)] bg-[oklch(from_var(--color-danger)_l_c_h/0.08)] px-4 py-1.5 text-xs text-[var(--color-danger)]"
          : "flex items-center gap-2 border-b border-[var(--color-warning)] bg-[oklch(from_var(--color-warning)_l_c_h/0.08)] px-4 py-1.5 text-xs text-[var(--color-warning)]"
      }
    >
      {isExhausted ? (
        <>
          <WifiOff className="size-3.5" aria-hidden />
          <span>
            Lost the event stream. Refresh the page to resume — your sandbox
            and progress are safe.
          </span>
        </>
      ) : (
        <>
          <Loader2 className="size-3.5 animate-spin" aria-hidden />
          <span>
            Reconnecting to the event stream… your sandbox is still running.
          </span>
        </>
      )}
    </div>
  );
}

/**
 * Grading-in-flight screen with an elapsed-time escalation: most submissions
 * grade in 5–30s, but if it crosses 30s/60s we widen the language so the user
 * knows the page is still alive rather than wondering whether to refresh.
 */
function GradingWait({ sessionId: _sessionId }: { sessionId: string }) {
  const [elapsed, setElapsed] = React.useState(0);
  React.useEffect(() => {
    const id = window.setInterval(() => setElapsed((s) => s + 1), 1_000);
    return () => window.clearInterval(id);
  }, []);
  const body =
    elapsed > 60
      ? "Still grading. Tougher mission packs can take up to two minutes — leave this tab open."
      : elapsed > 30
        ? "Almost there… hidden tests on this mission are a bit heavier than usual."
        : "Running hidden tests and validators. This usually takes 5–30 seconds.";
  return (
    <FullPageMessage
      tone="info"
      icon={<Loader2 aria-hidden className="size-6 animate-spin" />}
      heading="Grading your submission"
      body={body}
    >
      <div
        className="mt-4 h-2 w-64 overflow-hidden rounded-full bg-[var(--color-muted)]"
        role="progressbar"
        aria-valuetext={`Grading in progress, ${elapsed} seconds elapsed`}
        aria-busy
      >
        <div className="h-full w-1/3 animate-[shimmer_1.4s_linear_infinite] rounded-full bg-[var(--color-primary)]" />
      </div>
      <p
        aria-hidden
        className="mt-2 font-mono text-[11px] text-[var(--color-muted-foreground)]"
      >
        {elapsed}s elapsed
      </p>
    </FullPageMessage>
  );
}

interface FullPageMessageProps {
  tone: "info" | "warning" | "danger";
  icon: React.ReactNode;
  heading: string;
  body: string;
  actions?: React.ReactNode;
  children?: React.ReactNode;
}

function FullPageMessage({
  tone,
  icon,
  heading,
  body,
  actions,
  children,
}: FullPageMessageProps) {
  const toneClass =
    tone === "danger"
      ? "text-[var(--color-danger)]"
      : tone === "warning"
        ? "text-[var(--color-warning)]"
        : "text-[var(--color-primary)]";
  return (
    <div className="flex h-[calc(100dvh-3.5rem)] flex-col items-center justify-center gap-3 text-center">
      <span className={toneClass}>{icon}</span>
      <p className="text-base font-semibold">{heading}</p>
      <p className="max-w-md text-sm text-[var(--color-muted-foreground)]">
        {body}
      </p>
      {children}
      {actions ? <div className="mt-3 flex items-center gap-2">{actions}</div> : null}
    </div>
  );
}
