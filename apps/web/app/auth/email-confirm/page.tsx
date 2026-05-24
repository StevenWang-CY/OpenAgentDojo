"use client";

/**
 * P0-6 — Email-change magic-link callback.
 *
 * The backend emails the new address a link of the form
 * ``{APP_URL}/auth/email-confirm?token=…`` during step 1 of the two-step
 * email-change flow. This page POSTs the token to
 * ``/api/v1/auth/me/email/confirm`` on mount, then:
 *
 *   - 200 (success) → the response carries the refreshed ``UserRead`` with
 *     the new email plus a freshly-minted session cookie. We surface a
 *     "Email updated to {new_email}" card and a CTA to ``/account``.
 *   - 400 (invalid/expired/wrong-user token, ``{code: "invalid_token"}``)
 *     / 404 / 422 → the magic link is no longer usable. We tell the user
 *     and link them back to ``/account`` so they can re-request from the
 *     email-change form.
 *   - 409 (``{code: "no_pending_email"}``) → there's no pending email
 *     change on the account anymore (link already consumed or the user
 *     cancelled). We surface a distinct "nothing to confirm" state so the
 *     user doesn't think the link itself was malformed.
 *   - 409 (``{code: "email_taken_in_flight"}``) → another account claimed
 *     the pending email before the confirm landed. We surface this as a
 *     distinct state so the user understands that *their request was
 *     valid* but the address is no longer available.
 *   - 401 (no session) → the user clicked the link in a browser where
 *     they're not signed in. We point them at ``/auth/sign-in`` first.
 *   - Network error → retry button.
 *
 * Mirrors ``app/auth/callback/page.tsx`` for the visual treatment so the
 * two callbacks feel like one cohesive auth surface.
 */

import * as React from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { AlertCircle, CheckCircle2, Loader2, RefreshCcw } from "lucide-react";
import type { User } from "@arena/shared-types";
import { ApiError, account } from "@/lib/api";
import { Header } from "@/components/layout/Header";
import { Button } from "@/components/ui/Button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/Card";

type ConfirmState =
  | { kind: "missing-token" }
  | { kind: "loading" }
  | { kind: "ok"; user: User }
  | { kind: "invalid" }
  | { kind: "no-pending" }
  | { kind: "taken-in-flight" }
  | { kind: "unauth" }
  | { kind: "network" }
  | { kind: "server" }
  | { kind: "rate-limited" };

export default function EmailConfirmPage() {
  const searchParams = useSearchParams();
  const token = searchParams.get("token");

  const [state, setState] = React.useState<ConfirmState>(
    token ? { kind: "loading" } : { kind: "missing-token" },
  );

  // Track the most recent controller so the Retry button can abort the
  // in-flight POST before kicking a fresh one, and so the effect cleanup
  // doesn't end up with two confirms racing.
  const controllerRef = React.useRef<AbortController | null>(null);

  const runConfirm = React.useCallback(
    async (currentToken: string, signal: AbortSignal) => {
      setState({ kind: "loading" });
      try {
        const user = await account.confirmEmailChange(
          { token: currentToken },
          signal,
        );
        if (signal.aborted) return;
        setState({ kind: "ok", user });
      } catch (err) {
        // The fetch wrapper turns aborts into ``ApiError(status=0)``. We
        // can't distinguish the two without sniffing the signal — short-
        // circuit any post-abort state writes so a leftover stale unmount
        // doesn't clobber the new page's state.
        if (signal.aborted) return;
        if (err instanceof ApiError) {
          // Two distinct 409 cases come from /me/email/confirm:
          //   * ``no_pending_email`` → there's nothing pending (link already
          //     used or cancelled).
          //   * ``email_taken_in_flight`` → the request was valid but the
          //     pending address was claimed by another account between
          //     change-request and confirm. The user needs to start over
          //     with a different address.
          // Branch on the body's ``code`` field; default any unrecognised
          // 409 to ``no-pending`` (the more conservative copy).
          if (err.status === 409) {
            const code =
              err.body &&
              typeof err.body === "object" &&
              "code" in err.body &&
              typeof (err.body as { code?: unknown }).code === "string"
                ? (err.body as { code: string }).code
                : null;
            if (code === "email_taken_in_flight") {
              setState({ kind: "taken-in-flight" });
              return;
            }
            setState({ kind: "no-pending" });
            return;
          }
          if (err.status === 400 || err.status === 404 || err.status === 422) {
            setState({ kind: "invalid" });
            return;
          }
          if (err.status === 401) {
            setState({ kind: "unauth" });
            return;
          }
          if (err.status === 429) {
            setState({ kind: "rate-limited" });
            return;
          }
          if (err.status >= 500) {
            setState({ kind: "server" });
            return;
          }
          if (err.status === 0) {
            // Pure connectivity failure (network unreachable / DNS) —
            // distinct from a backend 5xx.
            setState({ kind: "network" });
            return;
          }
        }
        // Any other status — fall back to network so the user always has
        // a Retry path rather than a frozen loader.
        setState({ kind: "network" });
      }
    },
    [],
  );

  const startConfirm = React.useCallback(
    (currentToken: string) => {
      controllerRef.current?.abort();
      const controller = new AbortController();
      controllerRef.current = controller;
      void runConfirm(currentToken, controller.signal);
    },
    [runConfirm],
  );

  React.useEffect(() => {
    if (!token) return;
    startConfirm(token);
    return () => {
      controllerRef.current?.abort();
      controllerRef.current = null;
    };
  }, [token, startConfirm]);

  if (state.kind === "missing-token") {
    return (
      <ConfirmShell tone="danger" Icon={AlertCircle} title="Missing token">
        <CardDescription>
          This confirmation link is missing its token. It may have already
          been used, or the URL got truncated in your email client. Head
          back to your account and request a new one.
        </CardDescription>
        <BackToAccount />
      </ConfirmShell>
    );
  }

  if (state.kind === "loading") {
    return (
      <PageChrome>
        <main className="flex flex-1 items-center justify-center px-6 py-12">
          <div className="flex flex-col items-center gap-4 text-center">
            <Loader2
              className="size-6 animate-spin text-[var(--color-primary)]"
              aria-hidden
            />
            <p className="text-sm font-medium">Confirming your new email&hellip;</p>
          </div>
        </main>
      </PageChrome>
    );
  }

  if (state.kind === "ok") {
    return (
      <ConfirmShell
        tone="primary"
        Icon={CheckCircle2}
        title="Email updated"
        data-testid="email-confirm-success"
      >
        <CardDescription>
          Your account email is now{" "}
          <span className="font-mono text-[var(--color-foreground)]">
            {state.user.email}
          </span>
          . Other sessions have been signed out as a precaution.
        </CardDescription>
        <Button asChild>
          <Link href="/account">Continue to account</Link>
        </Button>
      </ConfirmShell>
    );
  }

  if (state.kind === "no-pending") {
    return (
      <ConfirmShell
        tone="warning"
        Icon={AlertCircle}
        title="Nothing to confirm"
        data-testid="email-confirm-no-pending"
      >
        <CardDescription>
          No pending email change for this account. The link may have
          already been used or the request was cancelled from your account
          settings.
        </CardDescription>
        <BackToAccount />
      </ConfirmShell>
    );
  }

  if (state.kind === "taken-in-flight") {
    return (
      <ConfirmShell
        tone="warning"
        Icon={AlertCircle}
        title="That address was just claimed"
        data-testid="email-confirm-taken-in-flight"
      >
        <CardDescription>
          Another account claimed this email address between your request and
          this confirmation. Your account email was not changed. Head back to
          your account and start an email change with a different address.
        </CardDescription>
        <BackToAccount />
      </ConfirmShell>
    );
  }

  if (state.kind === "invalid") {
    return (
      <ConfirmShell
        tone="danger"
        Icon={AlertCircle}
        title="This link is invalid or has expired"
        data-testid="email-confirm-invalid"
      >
        <CardDescription>
          We couldn&rsquo;t confirm this email change. The link may have
          already been used, the request may have been cancelled, or no
          pending change exists for your account.
        </CardDescription>
        <BackToAccount />
      </ConfirmShell>
    );
  }

  if (state.kind === "unauth") {
    return (
      <ConfirmShell
        tone="danger"
        Icon={AlertCircle}
        title="You need to be signed in"
        data-testid="email-confirm-unauth"
      >
        <CardDescription>
          Email changes confirm against your current session. Sign in with
          your existing email, then re-click the link from your inbox.
        </CardDescription>
        <Button asChild>
          <Link href="/auth/sign-in">Sign in</Link>
        </Button>
      </ConfirmShell>
    );
  }

  if (state.kind === "server") {
    return (
      <ConfirmShell
        tone="danger"
        Icon={AlertCircle}
        title="Server hit a snag"
        data-testid="email-confirm-server"
      >
        <CardDescription>
          Our server hit a snag. Try again in a moment.
        </CardDescription>
        <Button onClick={() => token && startConfirm(token)}>
          <RefreshCcw className="size-4" aria-hidden /> Retry
        </Button>
      </ConfirmShell>
    );
  }

  if (state.kind === "rate-limited") {
    return (
      <ConfirmShell
        tone="warning"
        Icon={AlertCircle}
        title="Too many attempts"
        data-testid="email-confirm-rate-limited"
      >
        <CardDescription>
          You&rsquo;ve hit our rate limit. Wait a minute and retry.
        </CardDescription>
        <Button onClick={() => token && startConfirm(token)}>
          <RefreshCcw className="size-4" aria-hidden /> Retry
        </Button>
      </ConfirmShell>
    );
  }

  return (
    <ConfirmShell
      tone="danger"
      Icon={AlertCircle}
      title="Couldn't reach the server"
      data-testid="email-confirm-network"
    >
      <CardDescription>
        A network error stopped us from confirming the change. Check your
        connection and try again.
      </CardDescription>
      <Button onClick={() => token && startConfirm(token)}>
        <RefreshCcw className="size-4" aria-hidden /> Retry
      </Button>
    </ConfirmShell>
  );
}

interface ConfirmShellProps {
  Icon: React.ComponentType<{ className?: string; "aria-hidden"?: boolean }>;
  tone: "primary" | "danger" | "warning";
  title: string;
  children: React.ReactNode;
  "data-testid"?: string;
}

function ConfirmShell({
  Icon,
  tone,
  title,
  children,
  ...rest
}: ConfirmShellProps) {
  const iconClass =
    tone === "primary"
      ? "size-6 text-[var(--color-primary)]"
      : tone === "warning"
        ? "size-6 text-[var(--color-warning)]"
        : "size-6 text-[var(--color-danger)]";
  return (
    <PageChrome>
      <main className="flex flex-1 items-center justify-center px-6 py-12">
        <Card className="w-full max-w-md" {...rest}>
          <CardHeader className="items-center text-center">
            <Icon className={iconClass} aria-hidden />
            <CardTitle className="mt-2">{title}</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col items-center gap-3 text-center">
            {children}
          </CardContent>
        </Card>
      </main>
    </PageChrome>
  );
}

/**
 * Lightweight chrome wrapper that mirrors the marketing layout's header so
 * the email-confirm callback feels like a system page (brand, theme toggle,
 * a clear "← home" escape) rather than an orphaned card on a blank canvas.
 * We don't reach for the route-group layout because the route lives under
 * ``/auth/*`` to mirror ``/auth/callback`` and ``/auth/sign-in``; rendering
 * the Header inline keeps the URL stable.
 */
function PageChrome({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-dvh flex-col">
      <Header showCta={false} />
      {children}
    </div>
  );
}

function BackToAccount() {
  return (
    <Button asChild variant="secondary">
      <Link href="/account">Back to account</Link>
    </Button>
  );
}
