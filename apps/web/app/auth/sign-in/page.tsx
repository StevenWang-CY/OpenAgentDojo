"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { ArrowRight, ChevronDown, Loader2, Mail } from "lucide-react";
import { toast } from "sonner";
import { ApiError, auth } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { GithubMark } from "@/components/layout/GithubMark";
import { track } from "@/lib/telemetry";
import { Input } from "@/components/ui/Input";
import { BrandMark } from "@/components/layout/BrandMark";
import { env } from "@/lib/env";

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

// P0-10 — keep this in lockstep with the backend's
// ``MAGIC_LINK_RESEND_WINDOW_SECONDS`` (apps/api/app/auth/magic_link.py).
// The visible timer doubles as a UX promise; bumping the backend value
// without updating this constant lets the countdown finish before the
// API will actually honour another resend, which surfaces as a
// confusing 429 / throttled toast.
const RESEND_COOLDOWN_SECONDS = 60;

/**
 * Sanitise the optional ``?next=`` query parameter before threading it
 * into the magic-link request body. Only in-app paths are allowed; we
 * deliberately drop anything that could be used to bounce the freshly
 * signed-in user off to an attacker-controlled origin (open-redirect
 * defence-in-depth — the backend also validates server-side).
 */
function sanitizeNext(raw: string | null): string | null {
  if (raw === null) return null;
  if (raw.length === 0 || raw.length > 512) return null;
  // Must be a relative same-origin path; reject scheme-relative ``//evil``
  // and absolute URLs ``https://evil``.
  if (!raw.startsWith("/") || raw.startsWith("//")) return null;
  return raw;
}

export default function SignInPage() {
  const [email, setEmail] = React.useState("");
  const [submitting, setSubmitting] = React.useState(false);
  const [sent, setSent] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [resendSecondsLeft, setResendSecondsLeft] = React.useState<number>(0);
  const [resending, setResending] = React.useState(false);
  // P0-7 — feature-flag the "Continue with GitHub" button. Start ``null``
  // (loading) so the SSR pass renders the static layout without flashing
  // the button only to hide it once the probe resolves.
  const [githubEnabled, setGithubEnabled] = React.useState<boolean | null>(
    null,
  );
  const searchParams = useSearchParams();
  const router = useRouter();
  const oauthError = searchParams.get("error");
  const nextParam = sanitizeNext(searchParams.get("next"));

  React.useEffect(() => {
    let cancelled = false;
    auth
      .isGithubOAuthAvailable()
      .then((available) => {
        if (!cancelled) setGithubEnabled(available);
      })
      .catch(() => {
        if (!cancelled) setGithubEnabled(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  React.useEffect(() => {
    if (oauthError === "github_oauth_failed") {
      toast.error(
        "Couldn't sign you in via GitHub. Please try again or use the email link below.",
      );
      // Scrub ``?error=…`` from the URL after surfacing the toast so a
      // soft refresh (or a copy/paste of the same URL) doesn't re-fire
      // the toast on every mount.
      router.replace("/auth/sign-in", { scroll: false });
    }
  }, [oauthError, router]);

  // P0-10 — drive the resend countdown from a single interval so the
  // visible counter and the disabled-state flip atomically. The effect
  // re-arms whenever a fresh send resets the counter back to 60.
  React.useEffect(() => {
    if (resendSecondsLeft <= 0) return;
    const id = window.setInterval(() => {
      setResendSecondsLeft((prev) => (prev <= 1 ? 0 : prev - 1));
    }, 1000);
    return () => window.clearInterval(id);
  }, [resendSecondsLeft]);

  function handleGithubClick() {
    if (githubEnabled !== true) return;
    track("sign_in_github_clicked");
    auth.startGithubOAuth({ returnTo: "/missions" });
  }

  function handleGithubFromResendClick() {
    if (githubEnabled !== true) return;
    track("github_oauth_clicked_from_resend");
    auth.startGithubOAuth({ returnTo: "/missions" });
  }

  async function handleResend() {
    if (resending || resendSecondsLeft > 0) return;
    setResending(true);
    track("magic_link_resend_clicked");
    try {
      const { wait_seconds } = await auth.resendMagicLink({
        email: email.trim(),
        ...(nextParam ? { next: nextParam } : {}),
      });
      if (wait_seconds > 0) {
        // The server short-circuited because we're still inside the
        // cooldown — keep the timer accurate by syncing to the server's
        // view (always defensive, even if our local timer says we're
        // clear).
        setResendSecondsLeft(wait_seconds);
        toast.message(`Wait ${wait_seconds}s — we just sent one.`);
      } else {
        setResendSecondsLeft(RESEND_COOLDOWN_SECONDS);
        toast.success("Link resent. Check your inbox.");
      }
    } catch (err) {
      const message =
        err instanceof ApiError && err.status === 429
          ? `Too many requests — wait ${err.retryAfterSeconds ?? RESEND_COOLDOWN_SECONDS}s.`
          : err instanceof ApiError
            ? err.message
            : "Failed to resend the link.";
      toast.error(message);
    } finally {
      setResending(false);
    }
  }

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
      await auth.sendMagicLink({
        email: email.trim(),
        ...(nextParam ? { next: nextParam } : {}),
      });
      track("sign_in_requested");
      setSent(true);
      // P0-10 — arm the resend timer immediately on the initial send so
      // the user can't pound the resend button the moment the success
      // card appears.
      setResendSecondsLeft(RESEND_COOLDOWN_SECONDS);
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
              <PostSendCard
                email={email}
                resendSecondsLeft={resendSecondsLeft}
                resending={resending}
                onResend={handleResend}
                onUseDifferentEmail={() => setSent(false)}
                onGithubClick={
                  // P0-10 — only surface the GitHub fallback when the
                  // backend reports it enabled OR an operator explicitly
                  // forced the build-time env flag. ``null`` (still
                  // probing) renders the "currently unavailable" tooltip
                  // so the layout doesn't shift after the probe lands.
                  githubEnabled === true || env.githubOauthEnabledBuildtime
                    ? handleGithubFromResendClick
                    : null
                }
              />
            ) : (
              <div className="mt-7 flex flex-col gap-4">
                {githubEnabled === true ? (
                  <>
                    <Button
                      type="button"
                      variant="secondary"
                      className="w-full justify-center"
                      onClick={handleGithubClick}
                      data-testid="signin-github-button"
                    >
                      <GithubMark className="size-4" />
                      Continue with GitHub
                    </Button>
                    <div
                      className="flex items-center gap-3 font-mono text-[10.5px] uppercase tracking-[0.14em] text-[var(--color-muted-foreground)]"
                      aria-hidden
                    >
                      <span className="h-px flex-1 bg-[var(--color-border)]" />
                      <span>or email link</span>
                      <span className="h-px flex-1 bg-[var(--color-border)]" />
                    </div>
                  </>
                ) : null}
                <form
                  onSubmit={handleSubmit}
                  noValidate
                  className="flex flex-col gap-4"
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
              </div>
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

/**
 * P0-10 — Post-send card. Replaces the previous one-line "check your inbox"
 * message with four actionable sections so the user has a path forward
 * even when the magic-link email never arrives:
 *
 *   1. **Inbox**: confirms which address we sent to.
 *   2. **Troubleshooting**: collapsible accordion of common causes
 *      (spam, promotions, corporate filters). Opens by default on the
 *      first send so the user sees the advice without an extra click.
 *   3. **Resend**: 60-second cooldown button (the timer is driven by
 *      the parent so a single source of truth runs the math).
 *   4. **GitHub OAuth**: only rendered when ``onGithubClick`` is
 *      non-null. When ``null`` we surface a "currently unavailable"
 *      message rather than a dead button so users can tell the
 *      feature is intentionally off vs. broken.
 */
function PostSendCard({
  email,
  resendSecondsLeft,
  resending,
  onResend,
  onUseDifferentEmail,
  onGithubClick,
}: {
  email: string;
  resendSecondsLeft: number;
  resending: boolean;
  onResend: () => void;
  onUseDifferentEmail: () => void;
  onGithubClick: (() => void) | null;
}) {
  const [helpOpen, setHelpOpen] = React.useState(true);
  const resendDisabled = resending || resendSecondsLeft > 0;
  const resendLabel =
    resendSecondsLeft > 0
      ? `Resend in ${resendSecondsLeft}s`
      : resending
        ? "Resending…"
        : "Didn't get it? Resend link";

  return (
    <div
      data-testid="post-send-card"
      className="mt-7 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-elevated)] p-4"
    >
      <p className="font-mono text-[11px] uppercase tracking-[0.14em] text-[var(--color-muted-foreground)]">
        {"// link sent"}
      </p>
      <p className="mt-2 text-sm font-medium">Check your inbox.</p>
      <p className="mt-1 text-xs text-[var(--color-muted-foreground)]">
        If <span className="font-mono">{email}</span> is registered, a
        sign-in link is on its way. It expires in 30 minutes.
      </p>

      {/* Troubleshooting accordion */}
      <div className="mt-4 border-t border-[var(--color-border)] pt-4">
        <button
          type="button"
          aria-expanded={helpOpen}
          aria-controls="signin-help-blurb"
          onClick={() => setHelpOpen((open) => !open)}
          className="flex w-full items-center justify-between gap-2 text-left text-xs font-medium text-[var(--color-foreground)]"
        >
          <span className="inline-flex items-center gap-1.5">
            <Mail className="size-3.5" aria-hidden />
            Didn&rsquo;t get it? Try these
          </span>
          <ChevronDown
            className={`size-3.5 transition-transform ${
              helpOpen ? "rotate-180" : ""
            }`}
            aria-hidden
          />
        </button>
        {helpOpen ? (
          <ul
            id="signin-help-blurb"
            className="mt-3 space-y-2 text-xs leading-relaxed text-[var(--color-muted-foreground)]"
          >
            <li>
              Check your <strong>spam</strong>, <strong>promotions</strong>,{" "}
              <strong>focused</strong>, or <strong>other</strong> folder.
            </li>
            <li>
              Add{" "}
              <code className="font-mono text-[11px]">
                hello@openagentdojo.app
              </code>{" "}
              to your contacts so future links land in your inbox.
            </li>
            <li>
              Corporate mail filters often block automated senders. If
              you&rsquo;re on a work address and can&rsquo;t whitelist us,
              try a personal address.
            </li>
            <li>
              Still stuck?{" "}
              <Link
                href="/help/signin"
                className="underline underline-offset-2 hover:text-[var(--color-foreground)]"
              >
                Open the sign-in help page →
              </Link>
            </li>
          </ul>
        ) : null}
      </div>

      {/* Resend + alternative routes */}
      <div className="mt-4 flex flex-col gap-2">
        <Button
          type="button"
          variant="secondary"
          onClick={onResend}
          disabled={resendDisabled}
          aria-disabled={resendDisabled}
          data-testid="resend-button"
          className="w-full justify-center"
        >
          {resending ? (
            <Loader2 className="size-4 animate-spin" aria-hidden />
          ) : null}
          <span>{resendLabel}</span>
        </Button>
        {onGithubClick ? (
          <Button
            type="button"
            variant="outline"
            onClick={onGithubClick}
            data-testid="github-oauth-cta"
            className="w-full justify-center"
            title="Sign in with GitHub instead"
          >
            <GithubMark className="size-4" />
            Or sign in with GitHub
          </Button>
        ) : (
          // P0-7 owns the backend probe; when disabled we surface a
          // helpful note rather than a dead button so users can tell
          // the feature is intentionally off vs. broken.
          <p
            data-testid="github-oauth-unavailable"
            className="text-center text-[11px] text-[var(--color-muted-foreground)]"
            title="GitHub sign-in is currently unavailable"
          >
            GitHub sign-in is currently unavailable
          </p>
        )}
        <button
          type="button"
          onClick={onUseDifferentEmail}
          className="mt-1 text-center text-[11px] text-[var(--color-muted-foreground)] underline underline-offset-2 hover:text-[var(--color-foreground)]"
        >
          Use a different email
        </button>
      </div>
    </div>
  );
}
