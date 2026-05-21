"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { AlertCircle, Loader2, RefreshCcw } from "lucide-react";
import { Button } from "@/components/ui/Button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/Card";
import { env } from "@/lib/env";
import { track } from "@/lib/telemetry";

/**
 * Magic-link callback page.
 *
 * The magic-link email points to `{APP_URL}/auth/callback?token=…`. We forward
 * the token to the backend callback endpoint over `fetch()` (credentials:
 * include) so we can observe the response and surface targeted errors:
 *
 *   - 200 / 204 / 302 → session cookie has been set; navigate to /missions
 *     (or the `next` query param if it points to a same-origin path).
 *   - 400 / 410       → token is invalid / expired / already used; show an
 *     inline error with a CTA to mint a fresh link.
 *   - network error   → surface the connectivity problem with a retry button.
 *
 * Previously we relied on `window.location.href = …` and trusted the backend
 * to render an HTML page on failure; that produced a noisy non-themed error
 * page when the token had expired. Owning the error rendering here keeps the
 * UX inside our design system.
 */

type CallbackState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ok" }
  | { kind: "expired" }
  | { kind: "network" };

/** Only honour `next` if it's a same-origin path — prevents open-redirect. */
function sanitiseNext(raw: string | null): string {
  if (!raw) return "/missions";
  if (!raw.startsWith("/") || raw.startsWith("//")) return "/missions";
  return raw;
}

export default function AuthCallbackPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const token = searchParams.get("token");
  const next = sanitiseNext(searchParams.get("next"));

  const [state, setState] = React.useState<CallbackState>(
    token ? { kind: "loading" } : { kind: "idle" }
  );

  const performCallback = React.useCallback(
    async (currentToken: string) => {
      setState({ kind: "loading" });
      const url = `${env.apiBaseUrl}/api/v1/auth/callback?token=${encodeURIComponent(
        currentToken
      )}`;
      try {
        // `redirect: "manual"` lets us treat a 3xx the same as a 2xx — both
        // mean the server validated the token and set the cookie. We never
        // want the browser to actually follow the redirect to /missions on
        // the API host (which would 404 since the app lives on a different
        // origin in production).
        const res = await fetch(url, {
          method: "GET",
          credentials: "include",
          redirect: "manual",
        });
        // `opaqueredirect` is what `redirect: "manual"` returns for 3xx in
        // browsers; treat it as success since the Set-Cookie has landed.
        const ok =
          res.ok ||
          res.status === 0 ||
          res.status === 302 ||
          (res.type as string) === "opaqueredirect";
        if (ok) {
          track("sign_in_completed");
          setState({ kind: "ok" });
          router.push(next);
          return;
        }
        if (res.status === 400 || res.status === 410) {
          setState({ kind: "expired" });
          return;
        }
        // Any other non-2xx — treat as network/server failure so the user
        // can retry instead of being stuck on a blank loader.
        setState({ kind: "network" });
      } catch {
        setState({ kind: "network" });
      }
    },
    [router, next]
  );

  React.useEffect(() => {
    if (!token) return;
    void performCallback(token);
  }, [token, performCallback]);

  if (!token) {
    return (
      <main className="flex min-h-dvh items-center justify-center px-6 py-12">
        <Card className="w-full max-w-md">
          <CardHeader className="items-center text-center">
            <AlertCircle
              className="size-6 text-[var(--color-danger)]"
              aria-hidden
            />
            <CardTitle className="mt-2">Invalid sign-in link</CardTitle>
            <CardDescription>
              This link is missing a token. It may have already been used or
              the URL was truncated.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex justify-center">
            <Button asChild variant="secondary">
              <Link href="/auth/sign-in">Back to sign-in</Link>
            </Button>
          </CardContent>
        </Card>
      </main>
    );
  }

  if (state.kind === "expired") {
    return (
      <main className="flex min-h-dvh items-center justify-center px-6 py-12">
        <Card className="w-full max-w-md">
          <CardHeader className="items-center text-center">
            <AlertCircle
              className="size-6 text-[var(--color-warning)]"
              aria-hidden
            />
            <CardTitle className="mt-2">This sign-in link has expired</CardTitle>
            <CardDescription>
              The link may have already been used or it&rsquo;s older than
              30 minutes. Send yourself a fresh one to keep going.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex justify-center">
            <Button onClick={() => router.push("/auth/sign-in")}>
              Send a new magic link
            </Button>
          </CardContent>
        </Card>
      </main>
    );
  }

  if (state.kind === "network") {
    return (
      <main className="flex min-h-dvh items-center justify-center px-6 py-12">
        <Card className="w-full max-w-md">
          <CardHeader className="items-center text-center">
            <AlertCircle
              className="size-6 text-[var(--color-danger)]"
              aria-hidden
            />
            <CardTitle className="mt-2">Couldn&rsquo;t reach the server</CardTitle>
            <CardDescription>
              We hit a network error while signing you in. Check your
              connection and try again.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col items-center gap-2">
            <Button onClick={() => void performCallback(token)}>
              <RefreshCcw className="size-4" aria-hidden /> Retry
            </Button>
            <Button asChild variant="ghost">
              <Link href="/auth/sign-in">Back to sign-in</Link>
            </Button>
          </CardContent>
        </Card>
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
