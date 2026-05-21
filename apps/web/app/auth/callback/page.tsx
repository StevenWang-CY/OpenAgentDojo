"use client";

import * as React from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { AlertCircle, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { env } from "@/lib/env";
import { track } from "@/lib/telemetry";

/**
 * Magic-link callback page.
 *
 * The magic-link email points to `{APP_URL}/auth/callback?token=…`. On mount
 * we forward the browser to the backend callback endpoint which validates the
 * token, sets the HttpOnly session cookie, then issues a 302 redirect to
 * /missions. This page therefore only stays visible for the instant it takes
 * for the browser to process the forward.
 *
 * If there is no token in the URL we show a clear error rather than a
 * silent blank screen.
 */
export default function AuthCallbackPage() {
  const searchParams = useSearchParams();
  const token = searchParams.get("token");

  React.useEffect(() => {
    if (!token) return;
    // Fire telemetry before navigating away — we never see this page render
    // again for this token. The backend's redirect-after-cookie-set is our
    // operational definition of "signed in".
    track("sign_in_completed");
    // Redirect to the backend callback. The backend validates, sets the cookie,
    // and issues a 302 to /missions — the browser follows it transparently.
    window.location.href = `${env.apiBaseUrl}/api/v1/auth/callback?token=${encodeURIComponent(token)}`;
  }, [token]);

  if (!token) {
    return (
      <main className="flex min-h-dvh items-center justify-center px-6 py-12">
        <div className="w-full max-w-md rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-8 text-center shadow-soft">
          <AlertCircle
            className="mx-auto size-6 text-[var(--color-danger)]"
            aria-hidden
          />
          <h1 className="mt-3 text-lg font-semibold tracking-tight">
            Invalid sign-in link
          </h1>
          <p className="mt-2 text-sm text-[var(--color-muted-foreground)]">
            This link is missing a token. It may have already been used or the
            URL was truncated.
          </p>
          <Button asChild variant="secondary" className="mt-6">
            <Link href="/auth/sign-in">Back to sign-in</Link>
          </Button>
        </div>
      </main>
    );
  }

  return (
    <main className="flex min-h-dvh items-center justify-center px-6 py-12">
      <div className="flex flex-col items-center gap-4 text-center">
        <Loader2
          className="size-6 animate-spin text-[var(--color-primary)]"
          aria-hidden
        />
        <p className="text-sm font-medium">Signing you in&hellip;</p>
        <p className="text-xs text-[var(--color-muted-foreground)]">
          You&rsquo;ll be redirected automatically.
        </p>
      </div>
    </main>
  );
}
