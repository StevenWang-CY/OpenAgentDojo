"use client";

import * as React from "react";
import { Bot, GitMerge, Send, User } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { AgentTurn } from "@arena/shared-types";
import { Button } from "@/components/ui/Button";
import { ScrollArea } from "@/components/ui/ScrollArea";
import { INTENT_KEYWORD_GROUPS } from "@/lib/intent-keywords";
import { cn } from "@/lib/utils";
import { track } from "@/lib/telemetry";

interface AgentChatProps {
  /** Turns to render. Newest at the bottom. */
  turns: AgentTurn[];
  /** Files currently in the agent's context. */
  contextPaths: string[];
  /** Submit a prompt. Returns once the API has responded. */
  onSubmit?(text: string): Promise<void> | void;
  /** Apply the patch proposed in a given turn. */
  onApplyPatch?(turnId: string): Promise<void> | void;
  className?: string;
}

export function AgentChat({
  turns,
  contextPaths,
  onSubmit,
  onApplyPatch,
  className,
}: AgentChatProps) {
  // The composer is always active in shipped surfaces; the legacy
  // ``disabled`` / ``pendingNote`` props were a holdover from the pre-M4
  // visual shell and were never wired up by any caller.
  const disabled = false;
  const [draft, setDraft] = React.useState("");
  const [submitting, setSubmitting] = React.useState(false);

  async function handleSubmit(e?: React.FormEvent | React.KeyboardEvent) {
    e?.preventDefault?.();
    const trimmed = draft.trim();
    if (!trimmed || disabled || !onSubmit) return;
    // PII-free analytics: we record length + context shape, never the prompt.
    track("prompt_submitted", {
      prompt_length: trimmed.length,
      context_files: contextPaths.length,
    });
    setSubmitting(true);
    try {
      await onSubmit(trimmed);
      setDraft("");
    } finally {
      setSubmitting(false);
    }
  }

  function applyHint(keyword: string) {
    setDraft((current) => (current.trim() ? `${current.trim()} ${keyword}` : keyword));
  }

  return (
    <div className={cn("flex h-full flex-col", className)}>
      <ScrollArea className="flex-1">
        <div
          role="log"
          aria-live="polite"
          aria-relevant="additions text"
          aria-label="Agent conversation"
          className="flex flex-col gap-4 p-4"
        >
          {turns.length === 0 ? (
            <EmptyState />
          ) : (
            turns.map((turn) => (
              <TurnView key={turn.id} turn={turn} onApplyPatch={onApplyPatch} />
            ))
          )}
        </div>
      </ScrollArea>

      <div className="border-t border-[var(--color-border)] bg-[var(--color-surface)] p-3">
        {contextPaths.length > 0 ? (
          <div className="mb-2 flex flex-wrap gap-1 text-[11px]">
            <span className="text-[var(--color-muted-foreground)]">Context:</span>
            {contextPaths.slice(0, 4).map((p) => (
              <span
                key={p}
                title={p}
                className="rounded bg-[var(--color-muted)] px-1.5 py-0.5 font-mono text-[10px]"
              >
                {basename(p)}
              </span>
            ))}
            {contextPaths.length > 4 ? (
              <span className="text-[var(--color-muted-foreground)]">
                +{contextPaths.length - 4} more
              </span>
            ) : null}
          </div>
        ) : null}

        <form
          onSubmit={(e) => {
            void handleSubmit(e);
          }}
          className="flex flex-col gap-2"
        >
          <label htmlFor="agent-prompt" className="sr-only">
            Prompt the agent
          </label>
          <textarea
            id="agent-prompt"
            value={draft}
            disabled={disabled || submitting}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                e.preventDefault();
                void handleSubmit(e);
              }
            }}
            rows={3}
            placeholder="Ask the agent to investigate, fix, or add a regression test. Cmd/Ctrl+Enter to send."
            className="resize-none rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-elevated)] px-3 py-2 font-mono text-xs leading-relaxed transition-colors duration-150 ease-macos focus-visible:outline-none focus-visible:border-[var(--color-ring)] focus-visible:ring-2 focus-visible:ring-[var(--color-ring)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--color-background)] disabled:cursor-not-allowed disabled:opacity-60"
          />
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex flex-wrap items-center gap-1">
              {INTENT_KEYWORD_GROUPS.flatMap((group) => group.keywords.slice(0, 1)).map(
                (keyword) => (
                  <button
                    type="button"
                    key={keyword}
                    onClick={() => applyHint(keyword)}
                    disabled={disabled || submitting}
                    title={`Insert: ${keyword}`}
                    className="rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-1.5 py-0.5 font-mono text-[10px] text-[var(--color-muted-foreground)] transition-colors duration-150 ease-macos hover:text-[var(--color-foreground)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-ring)] focus-visible:ring-offset-1 focus-visible:ring-offset-[var(--color-surface)] disabled:opacity-50"
                  >
                    {keyword}
                  </button>
                )
              )}
            </div>
            <Button
              type="submit"
              size="sm"
              disabled={disabled || submitting || !draft.trim()}
            >
              <Send className="size-3.5" aria-hidden />
              Send
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}

interface TurnViewProps {
  turn: AgentTurn;
  onApplyPatch?: (turnId: string) => Promise<void> | void;
}

function TurnView({ turn, onApplyPatch }: TurnViewProps) {
  const [applying, setApplying] = React.useState(false);
  const [appliedLocal, setAppliedLocal] = React.useState(false);

  const canApply =
    onApplyPatch !== undefined &&
    (turn.proposed_actions ?? []).includes("apply_patch") &&
    turn.applied_patch === null &&
    !appliedLocal;

  async function handleApply() {
    if (!onApplyPatch || applying) return;
    track("patch_applied", { turn_id: turn.id });
    setApplying(true);
    try {
      await onApplyPatch(turn.id);
      setAppliedLocal(true);
    } finally {
      setApplying(false);
    }
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-start gap-2">
        <Avatar role="user" />
        <div className="flex-1 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-elevated)] p-3">
          <p className="whitespace-pre-wrap text-sm leading-relaxed">
            {turn.user_prompt}
          </p>
        </div>
      </div>
      <div className="flex items-start gap-2">
        <Avatar role="agent" />
        <div className="flex-1 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3">
          <div className="prose prose-sm prose-neutral max-w-none dark:prose-invert">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {turn.agent_response}
            </ReactMarkdown>
          </div>
          {turn.applied_patch || appliedLocal ? (
            <p className="mt-2 text-[11px] text-[var(--color-muted-foreground)]">
              Patch applied. Open the diff viewer to review.
            </p>
          ) : null}
          {canApply ? (
            <div className="mt-3" data-tutorial-anchor="apply-patch">
              <Button
                size="sm"
                variant="secondary"
                disabled={applying}
                onClick={() => void handleApply()}
              >
                <GitMerge className="size-3.5" aria-hidden />
                {applying ? "Applying…" : "Apply Patch"}
              </Button>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function Avatar({ role }: { role: "user" | "agent" }) {
  return (
    <span
      aria-hidden
      className={cn(
        "mt-0.5 grid size-7 shrink-0 place-items-center rounded-full",
        role === "user"
          ? "bg-[var(--color-muted)] text-[var(--color-foreground)]"
          : "bg-[oklch(from_var(--color-primary)_l_c_h/0.18)] text-[var(--color-primary)]"
      )}
    >
      {role === "user" ? <User className="size-3.5" /> : <Bot className="size-3.5" />}
    </span>
  );
}

function EmptyState() {
  return (
    <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-[var(--color-border)] p-6 text-center text-xs text-[var(--color-muted-foreground)]">
      <Bot className="size-4" aria-hidden />
      <p className="font-medium text-[var(--color-foreground)]">
        Prompt the agent to begin.
      </p>
      <p className="max-w-xs">
        Tip: tell the agent what to investigate, what files to touch, and
        whether you want a regression test. The keyword chips below are
        suggestions, not commands.
      </p>
    </div>
  );
}

function basename(path: string): string {
  const idx = path.lastIndexOf("/");
  return idx === -1 ? path : path.slice(idx + 1);
}
