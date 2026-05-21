"use client";

import * as React from "react";
import { useMutation } from "@tanstack/react-query";
import { CheckCircle2, FlaskConical, Loader2, Type, Wand2, XCircle } from "lucide-react";
import { toast } from "sonner";
import { ApiError, runCommand } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/utils";
import type { CommandCategory, CommandRun } from "@arena/shared-types";

interface TestAction {
  category: CommandCategory;
  command: string;
  label: string;
  icon: typeof FlaskConical;
}

interface TestPanelProps {
  sessionId: string;
  /**
   * Commands to expose. Defaults to a Node-ish trio that matches
   * `fullstack-auth-demo`; missions can override via the workspace shell.
   */
  actions?: TestAction[];
  className?: string;
}

const DEFAULT_ACTIONS: TestAction[] = [
  { category: "test", command: "pnpm test:unit", label: "Unit", icon: FlaskConical },
  { category: "typecheck", command: "pnpm typecheck", label: "Typecheck", icon: Type },
  { category: "lint", command: "pnpm lint", label: "Lint", icon: Wand2 },
];

interface ActionState {
  status: "idle" | "running" | "success" | "failure";
  exit_code?: number | null;
  duration_ms?: number | null;
}

export function TestPanel({
  sessionId,
  actions = DEFAULT_ACTIONS,
  className,
}: TestPanelProps) {
  const [state, setState] = React.useState<Record<string, ActionState>>({});

  const mutation = useMutation<
    CommandRun,
    Error,
    TestAction
  >({
    mutationFn: (action) =>
      runCommand(sessionId, { command: action.command, category: action.category }),
    onMutate(action) {
      setState((s) => ({ ...s, [action.command]: { status: "running" } }));
    },
    onSuccess(data, action) {
      const ok = (data.exit_code ?? 0) === 0;
      setState((s) => ({
        ...s,
        [action.command]: {
          status: ok ? "success" : "failure",
          exit_code: data.exit_code,
          duration_ms: data.duration_ms,
        },
      }));
      if (!ok) {
        toast.warning(`${action.label} exited ${data.exit_code ?? "?"}`);
      }
    },
    onError(error, action) {
      setState((s) => ({ ...s, [action.command]: { status: "failure" } }));
      const msg =
        error instanceof ApiError
          ? error.status === 0
            ? "Couldn't reach the API. Is the backend running?"
            : error.message
          : `Failed to run ${action.label}`;
      toast.error(msg);
    },
  });

  // Build a screen-reader announcement for the most recently-finished action.
  const liveLine = React.useMemo(() => {
    const entries = Object.entries(state).filter(
      ([, s]) => s.status === "success" || s.status === "failure"
    );
    const tail = entries[entries.length - 1];
    if (!tail) return "";
    const [cmd, last] = tail;
    const action = actions.find((a) => a.command === cmd);
    if (!action) return "";
    return last.status === "success"
      ? `${action.label} passed.`
      : `${action.label} failed${last.exit_code != null ? ` (exit ${last.exit_code})` : ""}.`;
  }, [state, actions]);

  return (
    <div className={cn("flex flex-col gap-2 p-3", className)}>
      <p className="text-[10px] font-semibold uppercase tracking-wide text-[var(--color-muted-foreground)]">
        Quick checks
      </p>
      <div role="status" aria-live="polite" className="sr-only">
        {liveLine}
      </div>
      <div className="flex flex-wrap gap-1.5">
        {actions.map((a) => {
          const s = state[a.command]?.status ?? "idle";
          const Icon = a.icon;
          return (
            <Button
              key={a.command}
              size="sm"
              variant="secondary"
              onClick={() => mutation.mutate(a)}
              disabled={mutation.isPending && s === "running"}
            >
              {s === "running" ? (
                <Loader2 className="size-3.5 animate-spin" aria-hidden />
              ) : s === "success" ? (
                <CheckCircle2 className="size-3.5 text-[var(--color-success)]" aria-hidden />
              ) : s === "failure" ? (
                <XCircle className="size-3.5 text-[var(--color-danger)]" aria-hidden />
              ) : (
                <Icon className="size-3.5" aria-hidden />
              )}
              {a.label}
              {state[a.command]?.duration_ms !== undefined &&
              state[a.command]?.duration_ms !== null ? (
                <span className="ml-1 font-mono text-[10px] text-[var(--color-muted-foreground)]">
                  {Math.round((state[a.command]?.duration_ms ?? 0) / 100) / 10}s
                </span>
              ) : null}
            </Button>
          );
        })}
      </div>
    </div>
  );
}
