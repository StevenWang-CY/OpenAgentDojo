"use client";

import * as React from "react";
import Link from "next/link";
import { ArrowRight, Loader2, Mail } from "lucide-react";
import { toast } from "sonner";
import { ApiError, auth } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { track } from "@/lib/telemetry";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/Card";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";

// Intentionally loose: a `local@host.tld` shape covers the vast majority of
// real-world typos without re-implementing RFC 5322. The backend is the
// authoritative validator; this is a UX-only fast path.
const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export default function SignInPage() {
  const [email, setEmail] = React.useState("");
  const [submitting, setSubmitting] = React.useState(false);
  const [sent, setSent] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (submitting) return;
    const trimmed = email.trim();
    // Empty (or whitespace-only) input is the most common form-submission
    // mistake; reject it loudly so users aren't left wondering why nothing
    // happened. We use the same error surface as the rest of the form.
    if (!trimmed) {
      const message = "Please enter a valid email address.";
      setError(message);
      toast.error(message);
      return;
    }
    // RFC 5321 caps the total email length at 254 octets; rejecting before
    // we touch the API gives the user a clearer error than waiting for a
    // 422 from the backend, and saves a roundtrip on obviously malformed
    // pastes (e.g. a 5KB JWT pasted by mistake).
    if (trimmed.length > 254) {
      const message = "Email is too long.";
      setError(message);
      toast.error(message);
      return;
    }
    // Cheap structural sanity check — the backend will still do the real
    // validation, but a clear in-form error beats a generic 422 from the
    // API for obvious typos like missing "@" or missing TLD.
    if (!EMAIL_PATTERN.test(trimmed)) {
      const message = "Please enter a valid email address.";
      setError(message);
      toast.error(message);
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await auth.sendMagicLink({ email: email.trim() });
      // PII-free: we never send the email. Only that a request was made.
      track("sign_in_requested");
      setSent(true);
      toast.success("Check your inbox for a sign-in link.");
    } catch (err) {
      // Network failures and API errors both surface here; never flip `sent`
      // optimistically — that hides real problems the user must address.
      const message =
        err instanceof ApiError
          ? err.status === 0
            ? "Couldn't reach the API. Check your connection and try again."
            : err.message
          : "Failed to send magic link.";
      setError(message);
      toast.error(message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="flex min-h-dvh items-center justify-center px-6 py-12">
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle>Sign in to OpenAgentDojo</CardTitle>
          <CardDescription>
            We&rsquo;ll email you a one-time sign-in link. No passwords.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {sent ? (
            <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-elevated)] p-4 text-sm">
              <Mail
                className="size-5 text-[var(--color-primary)]"
                aria-hidden
              />
              <p className="mt-2 font-medium">Check your email.</p>
              <p className="mt-1 text-xs text-[var(--color-muted-foreground)]">
                If <span className="font-mono">{email}</span> is registered, a
                sign-in link is on its way. The link expires in 30 minutes.
              </p>
              <Button
                variant="link"
                className="mt-3"
                onClick={() => setSent(false)}
              >
                Use a different email
              </Button>
            </div>
          ) : (
            <form
              onSubmit={handleSubmit}
              noValidate
              className="flex flex-col gap-4"
            >
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="email">Email address</Label>
                <Input
                  id="email"
                  type="email"
                  autoComplete="email"
                  required
                  maxLength={254}
                  placeholder="you@example.com"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  disabled={submitting}
                  aria-invalid={error ? true : undefined}
                  aria-describedby={error ? "signin-error" : undefined}
                />
              </div>
              {error ? (
                <p
                  id="signin-error"
                  role="alert"
                  className="rounded-md border border-[oklch(from_var(--color-danger)_l_c_h/0.5)] bg-[oklch(from_var(--color-danger)_l_c_h/0.08)] px-3 py-2 text-xs text-[var(--color-danger)]"
                >
                  {error}
                </p>
              ) : null}
              <Button type="submit" disabled={submitting}>
                {submitting ? (
                  <Loader2 className="size-4 animate-spin" aria-hidden />
                ) : (
                  <ArrowRight className="size-4" aria-hidden />
                )}
                {submitting ? "Sending…" : "Email me a sign-in link"}
              </Button>
            </form>
          )}
        </CardContent>
        <CardContent className="border-t border-[var(--color-border)] pt-4 text-xs text-[var(--color-muted-foreground)]">
          By signing in you agree to use OpenAgentDojo&rsquo;s sandboxes responsibly.
          <span className="block mt-2">
            <Link
              href="/"
              className="underline-offset-2 hover:text-[var(--color-foreground)] hover:underline"
            >
              Back to landing
            </Link>
          </span>
        </CardContent>
      </Card>
    </main>
  );
}
