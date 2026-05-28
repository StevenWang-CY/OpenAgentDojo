"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
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
  auth,
  getDiff,
  getFileTree,
  getSession,
  getSubmission,
  getTimeline,
  getWsToken,
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
import { CommandPalette } from "./CommandPalette";
import { DiffViewer } from "./DiffViewer";
import { HelpOverlay, shouldAutoOpenHelp } from "./HelpOverlay";
import { SearchPanel } from "./SearchPanel";
import { Terminal } from "./Terminal";
import { TestPanel } from "./TestPanel";
import { AgentChat } from "./AgentChat";
import { Timeline } from "./Timeline";
import { MissionBrief } from "./MissionBrief";
import { ScorePreview } from "./ScorePreview";
import { WorkspaceTopBar } from "./WorkspaceTopBar";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/Tabs";
import { Button } from "@/components/ui/Button";
import { TutorialController } from "@/components/tutorial/TutorialController";
import { useWorkspaceShortcuts } from "@/lib/keyboard";
import { IntegritySignaller } from "@/lib/integrity";

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
  const pathname = usePathname();
  const queryClient = useQueryClient();
  // P1 — Scratchpad localStorage scoping fix: thread the authenticated
  // ``user_id`` through ``useWorkspaceStore`` so the persist key is
  // ``arena:workspace:${userId}:${sessionId}`` instead of the previous
  // session-only key. A user switch on the same machine no longer reuses
  // the previous account's slice. The query is keyed on ``["me"]`` so it
  // shares cache with the existing ``meQuery`` below (no extra round
  // trip). ``meId`` is ``null`` until the first ``/auth/me`` lands — the
  // store falls back to ``"anon"`` for that brief window, then snaps to
  // the user-scoped key on the next render.
  const meIdQuery = useQuery({
    queryKey: ["me"],
    queryFn: ({ signal }) => auth.me(signal),
    staleTime: 60_000,
    retry: false,
  });
  const meId = meIdQuery.data?.id ?? null;
  const store = useWorkspaceStore(sessionId, meId);

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
  // P0-9 — quick-open / find-in-files / help overlay surfaces.
  const commandPaletteOpen = store((s) => s.commandPaletteOpen);
  const searchPanelOpen = store((s) => s.searchPanelOpen);
  const helpOverlayOpen = store((s) => s.helpOverlayOpen);
  const setCommandPaletteOpen = store((s) => s.setCommandPaletteOpen);
  const setSearchPanelOpen = store((s) => s.setSearchPanelOpen);
  const setHelpOverlayOpen = store((s) => s.setHelpOverlayOpen);
  const setActivePath = store((s) => s.setActivePath);
  // P1-4 — scratchpad open/closed; Cmd/Ctrl+B toggles, click-toggle from
  // the pane header keeps these in lockstep with the persisted user pref.
  const scratchpadOpen = store((s) => s.scratchpadOpen);
  const setScratchpadOpen = store((s) => s.setScratchpadOpen);

  // Controlled tab state — `openFile` flips us to the editor tab so the
  // user lands on the file they just clicked instead of an old surface.
  const [tab, setTab] = React.useState<WorkspaceTab>("editor");

  // P0-9 — global workspace shortcuts (quick-open, find-in-files, help,
  // close). The hook itself is mounted unconditionally (React rules of
  // hooks) but only fires while the workspace is active. Callbacks below
  // toggle the relevant store flags; the actual overlay components live
  // further down the tree and bind to those flags.
  useWorkspaceShortcuts({
    on: {
      "quick-open": () => {
        setCommandPaletteOpen(true);
      },
      "find-in-files": () => {
        setSearchPanelOpen(true);
      },
      "help-overlay": () => {
        setHelpOverlayOpen(!helpOverlayOpen);
      },
      "toggle-scratchpad": () => {
        // P1-4 — keyboard toggle for the scratchpad. The pane's own
        // header click is the discoverable surface; this gives power
        // users a one-key path. Telemetry distinguishes the two via
        // the ``trigger`` property on ``scratchpad_opened`` (Item 29
        // — pass "keybind" through the store's trigger field).
        setScratchpadOpen(!scratchpadOpen, "keybind");
      },
      escape: () => {
        // Close the topmost overlay in z-order. The CommandPalette + help
        // overlay use Radix Dialog so Escape already closes them via the
        // overlay's own handler; we only need to handle the side-panel
        // explicitly because it doesn't use Dialog.
        if (searchPanelOpen) setSearchPanelOpen(false);
      },
    },
  });

  // First-time visitors auto-open the help overlay once. The check happens
  // on mount only — subsequent renders never re-open it. ``setHelpOverlayOpen``
  // is a Zustand setter (stable across renders) so listing it in the deps
  // doesn't re-fire the effect.
  React.useEffect(() => {
    if (shouldAutoOpenHelp()) {
      setHelpOverlayOpen(true);
    }
  }, [setHelpOverlayOpen]);

  // Live WS connection state for the events stream — drives the unobtrusive
  // banner that tells the user "reconnecting…" so transient drops don't look
  // like the app froze. Defaults to "open" so we don't flash a banner during
  // the brief window before the first connect.
  const [wsStatus, setWsStatus] = React.useState<ReconnectingSocketStatus>("open");

  // Flipped when the backend closes the events stream with code 4404
  // (session reaped). The shell then renders a terminal "session ended"
  // view rather than the generic reconnecting banner — there's nothing to
  // recover, so the only useful affordances are "start a new mission" and
  // "open your profile".
  const [sessionEnded, setSessionEnded] = React.useState(false);

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
  // We mint a fresh short-lived WS token via `getWsToken` rather than relying
  // on the one embedded in `SessionDetail` — the latter is captured at
  // session-fetch time and would already be stale (60s TTL) for any session
  // the user keeps open more than a minute. The query is gated on
  // `status === "active" | "submitting"` so we don't fire it during
  // provisioning or after a graded/abandoned terminal state.
  const tokenQueryEnabled = status === "active" || status === "submitting";
  // Fetch the authenticated user so the "Session ended" terminal view can
  // deep-link to the correct ``/profile/{handle}`` page. Cheap, cached, and
  // safe to keep mounted — ``auth.me()`` is a tiny GET that the layout
  // already primes elsewhere via React Query's shared cache. Aliased to
  // the workspace-store-scoping query above (same ``["me"]`` key → same
  // cache entry → no extra round trip).
  const meQuery = meIdQuery;

  const tokenQuery = useQuery({
    queryKey: ["ws-token", sessionId],
    queryFn: ({ signal }) => getWsToken(sessionId, signal),
    enabled: tokenQueryEnabled,
    // The token only lives for 60s on the server. Force a fresh mint on every
    // mount/window-focus so a tab that comes back from background has a valid
    // token in hand before the WS effect re-runs.
    staleTime: 30_000,
    refetchOnWindowFocus: true,
    retry: (failureCount, error) => {
      if (error instanceof ApiError && error.status === 401) return false;
      return failureCount < 1;
    },
  });

  // Centralised 401 handler — covers both an initial token-mint that comes
  // back unauthorised and a later WS close-code 4401 that exhausts. Either
  // way we punt the user back to /auth/sign-in with the current pathname so
  // they land back here after re-authenticating.
  const redirectToSignIn = React.useCallback(() => {
    const next = pathname ?? `/workspace/${sessionId}`;
    router.push(`/auth/sign-in?next=${encodeURIComponent(next)}`);
  }, [pathname, router, sessionId]);

  React.useEffect(() => {
    if (!tokenQueryEnabled) return;
    if (tokenQuery.error instanceof ApiError && tokenQuery.error.status === 401) {
      redirectToSignIn();
    }
  }, [tokenQueryEnabled, tokenQuery.error, redirectToSignIn]);

  const wsToken = tokenQuery.data?.token;
  const sessionLoaded = !!sessionQuery.data;

  // Tracks the latest moment we flipped into `error` so we can keep the WS
  // open for a short trailing window — long enough to ingest a
  // `submission.failed` frame that the backend may emit *just after* the
  // session row transitions. The window is cleared once the event lands or
  // 3s elapses, whichever is first. We pair the timestamp ref with a
  // state-backed "open" flag so the WS-attach effect actually re-runs when
  // the window closes; without it the boolean was only ever recomputed on
  // an unrelated render and the socket would linger open past the deadline.
  const errorIngestionWindowRef = React.useRef<number | null>(null);
  const [errorWindowOpen, setErrorWindowOpen] = React.useState(false);
  // Authoritative "we already saw submission.failed" check used by both
  // the trailing-WS window below and the status-branch render below.
  const submissionFailed = React.useMemo(
    () => events.some((e) => e.event_type === "submission.failed"),
    [events]
  );

  React.useEffect(() => {
    if (status === "error") {
      if (errorIngestionWindowRef.current === null) {
        errorIngestionWindowRef.current = Date.now();
        setErrorWindowOpen(true);
        const timer = setTimeout(() => {
          setErrorWindowOpen(false);
        }, 3_000);
        return () => {
          clearTimeout(timer);
        };
      }
      return;
    }
    if (errorIngestionWindowRef.current !== null) {
      errorIngestionWindowRef.current = null;
      setErrorWindowOpen(false);
    }
  }, [status]);

  // Hold a stable ref to ``tokenQuery.refetch`` so the WS effect can call it
  // from ``resolveQueryParams`` without keeping ``tokenQuery`` (a freshly-
  // allocated object on every render) in its deps array. Previously the
  // effect re-mounted the socket on every render — including renders
  // triggered by the refetch it called itself — which produced a thundering-
  // herd of reconnects and dropped live events between teardowns.
  const tokenRefetchRef = React.useRef(tokenQuery.refetch);
  React.useEffect(() => {
    tokenRefetchRef.current = tokenQuery.refetch;
  }, [tokenQuery.refetch]);

  React.useEffect(() => {
    if (!sessionLoaded) return;
    if (!wsToken) return;
    const trailingErrorWindowOpen =
      status === "error" && !submissionFailed && errorWindowOpen;
    if (
      status !== "active" &&
      status !== "submitting" &&
      !trailingErrorWindowOpen
    ) {
      return;
    }

    const socket = createReconnectingSocket({
      url: `/ws/sessions/${sessionId}/events`,
      token: wsToken,
      sessionId,
      // Resolved synchronously on every (re)connect, so reconnects pick up
      // the freshest `lastEventId` from the ref — no need for the effect
      // to re-mount on every event. We also refetch the WS token on each
      // reconnect to side-step the 60s TTL.
      resolveQueryParams: () => {
        // Best-effort refresh; the token-mint is async so any in-flight
        // result is picked up by the next reconnect. Read through the ref
        // so an inline call doesn't pin ``tokenQuery`` in the deps array.
        void tokenRefetchRef.current();
        return lastEventIdRef.current > 0
          ? { last_id: lastEventIdRef.current }
          : {};
      },
      onAuthFailure: () => {
        redirectToSignIn();
      },
      onSessionEnded: () => {
        setSessionEnded(true);
      },
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
    submissionFailed,
    errorWindowOpen,
    pushEvent,
    pushAgentTurn,
    queryClient,
    redirectToSignIn,
  ]);

  // Catch the tail of the event stream once the session reaches a terminal
  // state. The WS is closed (or closing) by then; refetching the timeline
  // backfills anything the live stream might have missed during the brief
  // window before disconnect.
  React.useEffect(() => {
    if (status === "graded" || status === "error" || status === "abandoned") {
      void queryClient.invalidateQueries({
        queryKey: ["session", sessionId, "timeline"],
      });
    }
    // FE-P4 audit fix — a freshly graded session shifts the engine's
    // weakest-dim picture, so the cached ``/me/recommendations`` set is
    // stale. Invalidate on the `graded` transition so the catalog chip,
    // profile strip, and report footer all read the fresh ranking on
    // their next render.
    if (status === "graded") {
      void queryClient.invalidateQueries({ queryKey: ["me-recommendations"] });
    }
  }, [status, sessionId, queryClient]);

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
  // (`submissionFailed` is computed above next to the WS effect so both
  //  the trailing-window logic and the status-branch render below share
  //  a single source of truth.)

  // When the session flips to `graded`, look up the submission so we can
  // redirect to /report/{submission.id}. Tutorial missions are an exception:
  // they short-circuit the scoring pipeline and persist no Submission row,
  // so we route back to the catalog instead of attempting a report fetch
  // that would 404.
  // ``status`` and ``missionKind`` both derive from ``sessionQuery.data``
  // so in practice they hydrate together. The explicit ``!== undefined``
  // guards below are defensive — if any future refactor splits the
  // session query (e.g. polling status from a smaller endpoint), the
  // undefined case won't accidentally trigger a 404'ing
  // GET /sessions/{id}/submission for a tutorial.
  const missionKind = sessionQuery.data?.mission.kind;
  const submissionQuery = useQuery({
    queryKey: ["session", sessionId, "submission"],
    queryFn: ({ signal }) => getSubmission(sessionId, signal),
    enabled:
      status === "graded"
      && missionKind !== undefined
      && missionKind !== "tutorial",
    retry: false,
  });

  // P0-1 — refresh the cached /auth/me so the catalog's "// start here"
  // banner picks up the new ``tutorial_completed_at`` immediately, and
  // invalidate the missions list so any catalog-derived state re-renders.
  React.useEffect(() => {
    if (status === "graded" && missionKind === "tutorial") {
      void queryClient.invalidateQueries({ queryKey: ["me"] });
      void queryClient.invalidateQueries({ queryKey: ["missions"] });
      router.replace("/missions?tutorial=completed");
    }
  }, [status, missionKind, queryClient, router]);

  React.useEffect(() => {
    if (
      status === "graded"
      && missionKind !== undefined
      && missionKind !== "tutorial"
      && submissionQuery.data
    ) {
      router.replace(`/report/${submissionQuery.data.id}`);
    }
  }, [status, missionKind, submissionQuery.data, router]);

  // P0-8 — proctored mode integrity signaller. Attached only when the
  // session's posture says ``proctored`` AND the session is in an
  // interactive state (no point collecting signals on a graded or
  // abandoned session). The signaller's listeners are window/document
  // scoped, so even if the workspace re-renders the listeners are
  // attached exactly once.
  const sessionMode = sessionQuery.data?.mode;
  React.useEffect(() => {
    if (sessionMode !== "proctored") return;
    if (status !== "active" && status !== "submitting") return;
    const signaller = new IntegritySignaller({
      sessionId,
      mode: sessionMode,
    }).start();
    return () => {
      signaller.dispose();
    };
  }, [sessionId, sessionMode, status]);

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
  if (!session) {
    // `useQuery` reports `!isLoading && !error && !data` when the queryFn
    // resolves to `undefined` (e.g. an unexpected 204 or a fetcher bug).
    // Returning `null` would leave the user on a blank page with no recovery
    // path — surface a real error UI instead.
    return (
      <FullPageMessage
        tone="danger"
        icon={<AlertCircle aria-hidden className="size-6" />}
        heading="Session unavailable"
        body="We couldn't load this session. Refresh to try again, or return to your mission list."
        actions={
          <>
            <Button
              variant="secondary"
              onClick={() => void sessionQuery.refetch()}
            >
              <RefreshCcw className="size-4" aria-hidden /> Refresh
            </Button>
            <Button asChild variant="ghost">
              <Link href="/missions">Back to missions</Link>
            </Button>
          </>
        }
      />
    );
  }

  if (sessionEnded) {
    return (
      <FullPageMessage
        tone="warning"
        icon={<AlertCircle aria-hidden className="size-6" />}
        heading="Session ended"
        body="The backend ended this session. Start a new mission or open your profile to see past attempts."
        actions={
          <>
            <Button asChild>
              <Link href="/missions">Start a new mission</Link>
            </Button>
            {meQuery.data?.handle ? (
              <Button asChild variant="ghost">
                <Link href={`/profile/${meQuery.data.handle}`}>Open profile</Link>
              </Button>
            ) : null}
          </>
        }
      />
    );
  }

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
    const erroredEvent = events.find(
      (e) => e.event_type === "session.errored"
    );
    const failedPayload = (failedEvent?.payload ?? erroredEvent?.payload) as
      | { stage?: string; detail?: string }
      | undefined;
    const body = failedPayload
      ? `${failedPayload.stage ? `${failedPayload.stage}: ` : ""}${failedPayload.detail ?? "Unknown error."}`
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
        sessionStartedAt={session.started_at}
        showGiveUp={mission.kind !== "tutorial"}
        sessionMode={session.mode}
        integritySignalsCount={session.integrity_signals_count}
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
            <div
              className="flex-1 min-h-0"
              data-tutorial-anchor="file-tree"
            >
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
                <TabsTrigger value="diff" data-tutorial-anchor="diff-tab">
                  Diff
                </TabsTrigger>
              </TabsList>
              <p className="font-mono text-[11px] text-[var(--color-muted-foreground)]">
                {activeFile ?? "no file open"}
              </p>
            </div>
            <TabsContent
              value="editor"
              className="mt-0 flex-1 min-h-0"
              data-paste-target="editor"
            >
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
              <div
                data-tutorial-anchor="agent-chat"
                data-paste-target="agent_chat"
                className="contents"
              >
                <AgentChat
                  turns={agentTurns}
                  contextPaths={selectedContext}
                  sessionId={sessionId}
                  sessionStatus={status}
                  // P1-4 — mount the scratchpad pane inside the AgentChat
                  // column as soon as we know the session status. The
                  // pane itself defends with a read-only banner +
                  // ``disabled`` textarea when the status is anything
                  // other than ``active`` (graded / gave_up / abandoned
                  // / error / submitting), so the user can still review
                  // their notes on a finished session — previously
                  // gating on ``status === "active"`` here unmounted the
                  // pane and made the read-only branch unreachable.
                  showScratchpad={status !== undefined}
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
              </div>
            ),
          },
        ]}
        bottomTabs={[
          {
            id: "terminal",
            label: "Terminal",
            content: (
              <div data-paste-target="terminal" className="contents">
                <Terminal sessionId={sessionId} token={wsToken} />
              </div>
            ),
          },
          {
            id: "tests",
            label: "Tests",
            content: (
              <div data-tutorial-anchor="test-panel" className="contents">
                <TestPanel sessionId={sessionId} />
              </div>
            ),
          },
          {
            id: "timeline",
            label: "Timeline",
            content: <Timeline events={events} />,
          },
        ]}
      />
      <TutorialController
        sessionId={sessionId}
        events={events}
        enabled={mission.kind === "tutorial"}
      />
      <CommandPalette
        sessionId={sessionId}
        open={commandPaletteOpen}
        onOpenChange={setCommandPaletteOpen}
        onSelect={(p) => {
          setActivePath(p);
          setTab("editor");
        }}
      />
      {searchPanelOpen ? (
        <div
          role="complementary"
          aria-label="Find in files"
          className="fixed right-4 top-20 z-40 h-[60vh] w-[min(28rem,90vw)] overflow-hidden rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] shadow-elevated"
        >
          <SearchPanel
            sessionId={sessionId}
            open={searchPanelOpen}
            onClose={() => setSearchPanelOpen(false)}
            onSelect={(p, line) => {
              setActivePath(p, line);
              setTab("editor");
            }}
          />
        </div>
      ) : null}
      <HelpOverlay open={helpOverlayOpen} onOpenChange={setHelpOverlayOpen} />
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
    // FE-P1 audit fix — surface the toast AND re-throw so the caller
    // (AgentChat) keeps the user's typed prompt in the textarea. The
    // previous swallow cleared the draft on a backend 500, dumping the
    // user's words on the floor.
    toast.error(
      err instanceof ApiError ? err.message : "Failed to send prompt."
    );
    throw err;
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
    typeof v.id === "number" &&
    typeof v.session_id === "string" &&
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
 *
 * Returns `null` on a malformed payload so the caller doesn't push a junk
 * row into the store — the backend ought never to emit this, but a single
 * bad event from a future schema bump shouldn't be able to break the
 * chat surface.
 */
function synthesiseAgentTurn(event: SupervisionEvent): AgentTurn | null {
  if (event.event_type !== "agent.responded") return null;
  const payload = event.payload;
  if (!isValidAgentRespondedPayload(payload)) {
    // FE-P2 audit fix — name the offending field so a future schema
    // bump surfaces in DevTools as "missing turn_index" rather than as
    // an opaque "malformed payload" dump.
    const offendingField = firstInvalidAgentRespondedField(payload);
    console.warn(
      `[workspace] dropping malformed agent.responded payload (invalid field: ${offendingField ?? "unknown"})`,
      payload
    );
    return null;
  }
  return {
    id: `synthetic-${event.id}`,
    session_id: event.session_id,
    turn_index: payload.turn_index,
    user_prompt: "",
    selected_context: { files: [], logs: [], tests: [], extras: [] },
    agent_response: payload.response_summary,
    // Seed the action list so `AgentChat` renders the "Apply patch" CTA in
    // the brief window between the WS event arriving and the real `AgentTurn`
    // landing via `submitPrompt`. The real turn dedupes by `turn_index`.
    proposed_actions: ["apply_patch"],
    applied_patch: null,
    patch_applied_at: null,
    created_at: event.occurred_at,
  };
}

/**
 * Narrowing predicate for the `agent.responded` payload. Both fields are
 * required by the contract — anything else is a bug worth surfacing in
 * DevTools rather than papering over.
 */
function isValidAgentRespondedPayload(value: unknown): value is {
  turn_index: number;
  response_summary: string;
} {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v.turn_index === "number" &&
    Number.isFinite(v.turn_index) &&
    typeof v.response_summary === "string"
  );
}

/**
 * Sibling of `isValidAgentRespondedPayload` that returns the *name* of the
 * first invalid field instead of a boolean. Used exclusively in the warn
 * path so DevTools surfaces a precise reason ("turn_index" / "response_summary"
 * / "payload") rather than an opaque "malformed" string.
 */
function firstInvalidAgentRespondedField(value: unknown): string | null {
  if (typeof value !== "object" || value === null) return "payload";
  const v = value as Record<string, unknown>;
  if (typeof v.turn_index !== "number" || !Number.isFinite(v.turn_index)) {
    return "turn_index";
  }
  if (typeof v.response_summary !== "string") {
    return "response_summary";
  }
  return null;
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
