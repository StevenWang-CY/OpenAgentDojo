"use client";

import * as React from "react";
import Link from "next/link";
import { ArrowRight, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { ApiError, auth } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { track } from "@/lib/telemetry";
import { Input } from "@/components/ui/Input";
import { BrandMark } from "@/components/layout/BrandMark";

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
    if (!trimmed) {
      const message = "Please enter a valid email address.";
      setError(message);
      toast.error(message);
      return;
    }
    if (trimmed.length > 254) {
      const message = "Email is too long.";
      setError(message);
      toast.error(message);
      return;
    }
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
      track("sign_in_requested");
      setSent(true);
      toast.success("Check your inbox for a sign-in link.");
    } catch (err) {
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
    <main className="min-h-dvh">
      <div className="grid min-h-dvh grid-cols-1 lg:grid-cols-2">
        <aside className="relative isolate flex flex-col justify-between border-b border-[var(--color-border)] bg-[var(--color-surface)] px-8 py-12 lg:border-b-0 lg:border-r lg:px-14 lg:py-16">
          <div
            aria-hidden
            className="absolute inset-0 -z-10 [background-image:linear-gradient(to_right,oklch(from_var(--color-foreground)_l_c_h/0.04)_1px,transparent_1px),linear-gradient(to_bottom,oklch(from_var(--color-foreground)_l_c_h/0.04)_1px,transparent_1px)] [background-size:56px_56px] [mask-image:radial-gradient(ellipse_at_top,black_30%,transparent_75%)]"
          />
          <Link
            href="/"
            className="inline-flex items-center gap-2 text-sm font-semibold tracking-tight"
          >
            <BrandMark size={22} />
            <span className="inline-flex items-baseline gap-1.5">
              <span>OpenAgentDojo</span>
              <span className="font-mono text-xs font-normal text-[var(--color-muted-foreground)]">
                · supervisor training
              </span>
            </span>
          </Link>

          <div className="my-12 lg:my-0">
            <p className="font-mono text-[11px] uppercase tracking-[0.18em] text-[var(--color-muted-foreground)]">
              <span className="text-[var(--color-primary)]">{"//"}</span>{" "}
              mission 01 · diff review
            </p>
            <p className="mt-5 max-w-[36ch] text-pretty text-lg leading-snug">
              The agent&rsquo;s patch{" "}
              <em className="rounded-sm bg-[oklch(from_var(--color-warning)_l_c_h/0.2)] px-1 not-italic">
                looks
              </em>{" "}
              right. That&rsquo;s the trap. Train the eye that catches what
              the linter, the test runner, and your own first read all miss.
            </p>
            <p className="mt-4 font-mono text-[11px] text-[var(--color-muted-foreground)]">
              openagentdojo · v1
            </p>
          </div>

          <p className="font-mono text-[11px] text-[var(--color-muted-foreground)]">
            no passwords · magic links expire in 30 minutes
          </p>
        </aside>

        <section className="flex flex-col justify-center px-8 py-12 lg:px-14 lg:py-16">
          <div className="mx-auto w-full max-w-sm">
            <p className="font-mono text-[11px] uppercase tracking-[0.18em] text-[var(--color-muted-foreground)]">
              <span className="text-[var(--color-primary)]">{"//"}</span> sign
              in
            </p>
            <h1 className="mt-2 text-[26px] font-semibold tracking-tight">
              Welcome back.
            </h1>
            <p className="mt-2 text-sm text-[var(--color-muted-foreground)]">
              We&rsquo;ll email you a one-time sign-in link. No passwords.
            </p>

            {sent ? (
              <div className="mt-7 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-elevated)] p-4">
                <p className="font-mono text-[11px] uppercase tracking-[0.14em] text-[var(--color-muted-foreground)]">
                  {"// link sent"}
                </p>
                <p className="mt-2 text-sm font-medium">Check your inbox.</p>
                <p className="mt-1 text-xs text-[var(--color-muted-foreground)]">
                  If <span className="font-mono">{email}</span> is registered,
                  a sign-in link is on its way. It expires in 30 minutes.
                </p>
                <Button
                  variant="link"
                  className="mt-3 h-auto px-0 py-0"
                  onClick={() => setSent(false)}
                >
                  Use a different email
                </Button>
              </div>
            ) : (
              <form
                onSubmit={handleSubmit}
                noValidate
                className="mt-7 flex flex-col gap-4"
              >
                <div className="flex flex-col gap-1.5">
                  <label
                    htmlFor="email"
                    className="font-mono text-[10.5px] uppercase tracking-[0.14em] text-[var(--color-muted-foreground)]"
                  >
                    email
                  </label>
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
                    className="font-mono"
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
                <Button
                  type="submit"
                  disabled={submitting}
                  className="w-full justify-center"
                >
                  {submitting ? (
                    <Loader2 className="size-4 animate-spin" aria-hidden />
                  ) : null}
                  {submitting ? "Sending…" : "Email me a sign-in link"}
                  {!submitting ? (
                    <ArrowRight className="size-4" aria-hidden />
                  ) : null}
                </Button>
              </form>
            )}

            <p className="mt-8 text-xs leading-relaxed text-[var(--color-muted-foreground)]">
              By signing in you agree to use OpenAgentDojo&rsquo;s sandboxes
              responsibly.{" "}
              <Link
                href="/"
                className="underline underline-offset-2 hover:text-[var(--color-foreground)]"
              >
                Back to landing
              </Link>
              .
            </p>
          </div>
        </section>
      </div>
    </main>
  );
}
