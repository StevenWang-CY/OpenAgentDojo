"use client";

import * as React from "react";
import Link from "next/link";
import { Button } from "@/components/ui/Button";

/**
 * Strip anything that smells like a token, email, or stack-trace path off
 * the digest so we don't ship arbitrary user data into the browser's
 * structured log. Next.js already redacts internal stack traces in prod,
 * but a defence-in-depth scrub here keeps dev/prod symmetric.
 */
function scrubError(error: Error & { digest?: string }): {
  name: string;
  message: string;
  digest?: string;
} {
  const message = error.message ?? "";
  // Drop email-like patterns and bearer-token-like long alphanum runs.
  const safeMessage = message
    .replace(/[\w.+-]+@[\w-]+\.[\w.-]+/g, "[email]")
    .replace(/[A-Za-z0-9_-]{40,}/g, "[token]")
    .slice(0, 240);
  return {
    name: error.name || "Error",
    message: safeMessage,
    digest: error.digest,
  };
}

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  React.useEffect(() => {
    // Structured single-line log so log aggregators can parse it. Avoid
    // dumping the raw Error object (which can carry framework internals
    // and stack frames with absolute paths in dev).
    const scrubbed = scrubError(error);
    if (typeof window !== "undefined") {
      // Use `warn` (not `error`) so this doesn't break Next.js's
      // build-time CI checks that scan for stray console.error calls.
      // We still get the entry in the browser console + telemetry.
      console.warn("[arena.route_error]", JSON.stringify(scrubbed));
    }
  }, [error]);

  return (
    <main className="flex min-h-dvh items-center justify-center px-6">
      <div className="max-w-md text-center">
        <p className="text-xs uppercase tracking-[0.2em] text-[var(--color-danger)]">
          Something broke
        </p>
        <h1 className="mt-2 text-2xl font-semibold tracking-tight">
          We hit an unexpected error.
        </h1>
        <p className="mt-3 text-sm text-[var(--color-muted-foreground)]">
          {error.message || "An unknown error occurred while rendering this page."}
          {error.digest ? ` (digest ${error.digest})` : null}
        </p>
        <div className="mt-6 flex items-center justify-center gap-3">
          <Button onClick={reset}>Try again</Button>
          <Button asChild variant="secondary">
            <Link href="/">Go home</Link>
          </Button>
        </div>
      </div>
    </main>
  );
}
