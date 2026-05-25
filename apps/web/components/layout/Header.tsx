"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { LogOut, Moon, RotateCcw, Settings, Shield, Sun, User as UserIcon } from "lucide-react";
import { toast } from "sonner";
import { ApiError, auth, createSession, replayTutorial } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { useTheme } from "@/stores/themeStore";
import { cn } from "@/lib/utils";
import { getConsent, syncLocalConsentToServer } from "@/lib/consent";
import { BrandMark } from "./BrandMark";

interface HeaderProps {
  showCta?: boolean;
}

const NAV_LINKS = [
  { href: "/missions", label: "Missions" },
  { href: "/skills", label: "Skills" },
] as const;

export function Header({ showCta = true }: HeaderProps) {
  const pathname = usePathname();
  const router = useRouter();
  const { resolvedTheme, toggle } = useTheme();
  const [mounted, setMounted] = React.useState(false);
  React.useEffect(() => setMounted(true), []);

  const queryClient = useQueryClient();
  const meQuery = useQuery({
    queryKey: ["me"],
    queryFn: ({ signal }) => auth.me(signal),
    retry: (failureCount, error) => {
      // 401 means "not signed in" — don't retry, and don't treat as an error.
      if (error instanceof ApiError && (error.status === 401 || error.status === 0)) {
        return false;
      }
      return failureCount < 1;
    },
  });

  const replayTutorialMutation = useMutation({
    mutationFn: async () => {
      const user = await replayTutorial();
      const session = await createSession({ mission_id: "orientation" });
      return { user, session };
    },
    onSuccess({ session }) {
      void queryClient.invalidateQueries({ queryKey: ["me"] });
      // The catalog grid renders off the missions list + the user query
      // (for the "// start here" banner). Refreshing only `me` would
      // leave a stale banner the user could click through.
      void queryClient.invalidateQueries({ queryKey: ["missions"] });
      router.push(`/workspace/${session.id}`);
    },
    onError(error) {
      if (
        error instanceof ApiError &&
        error.status === 409 &&
        typeof error.body?.detail === "object" &&
        error.body.detail !== null &&
        "active_session_id" in error.body.detail
      ) {
        const activeId = (
          error.body.detail as { active_session_id?: string }
        ).active_session_id;
        toast.error(
          "You already have an active session — finish it first.",
        );
        if (activeId) router.push(`/workspace/${activeId}`);
        return;
      }
      toast.error(
        error instanceof ApiError
          ? error.message
          : "Failed to start the tutorial.",
      );
    },
  });

  const logoutMutation = useMutation({
    mutationFn: () => auth.logout(),
    onSuccess() {
      toast.success("Signed out.");
      // Wipe every cached query before navigating. Without this, the
      // previous user's /profile/me, /skills, mission/session, and
      // workspace caches stay live for their staleTime (≥ 60s for most
      // entries) — on a shared device the next user inherits the previous
      // user's data.
      queryClient.clear();
      router.push("/");
    },
    onError(error) {
      toast.error(
        error instanceof ApiError ? error.message : "Failed to sign out."
      );
    },
  });

  const user = meQuery.data && !meQuery.isError ? meQuery.data : null;
  const handle = user?.handle ?? null;

  // P0-5 — anonymous → signed-in transition. When `me` resolves to a real
  // user for the first time in this tab, sync any localStorage consent
  // choice up to the server so the audit trail picks up the pre-login
  // decision.
  //
  // ``syncedRef`` is set AFTER the await resolves so a transient 5xx during
  // the only login transition doesn't permanently lose the audit row — the
  // next focus / mount re-runs the effect and tries again. The flag is
  // re-armed on terminal failure (post-retry inside the helper) so the
  // retry has a meaningful trigger.
  const syncInFlightRef = React.useRef(false);
  const syncedRef = React.useRef(false);
  React.useEffect(() => {
    if (!user || syncedRef.current || syncInFlightRef.current) return;
    const local = getConsent();
    const hasAnyLocal =
      local.analytics !== null ||
      local.functional !== null ||
      local.marketing !== null;
    if (!hasAnyLocal) return;

    syncInFlightRef.current = true;
    void (async () => {
      try {
        await syncLocalConsentToServer(local);
        syncedRef.current = true;
      } catch {
        // helper already logged a single deduplicated [consent.sync] warn;
        // leaving syncedRef === false lets a later focus event retry.
        syncedRef.current = false;
      } finally {
        syncInFlightRef.current = false;
      }
    })();
  }, [user]);

  return (
    <header className="sticky top-0 z-40 border-b border-[var(--color-border)] bg-[oklch(from_var(--color-background)_l_c_h/0.85)] backdrop-blur supports-[backdrop-filter]:bg-[oklch(from_var(--color-background)_l_c_h/0.7)]">
      <div className="mx-auto flex h-14 max-w-6xl items-center justify-between px-6">
        <Link
          href="/"
          className="inline-flex items-center gap-2 text-sm font-semibold tracking-tight"
        >
          <BrandMark size={22} />
          <span className="inline-flex items-baseline gap-1.5">
            <span>OpenAgentDojo</span>
            <span className="font-mono text-xs font-normal tracking-normal text-[var(--color-muted-foreground)]">
              · supervisor training
            </span>
          </span>
        </Link>

        <nav className="hidden items-center gap-1 sm:flex">
          {NAV_LINKS.map((link) => {
            const active = pathname?.startsWith(link.href);
            return (
              <Link
                key={link.href}
                href={link.href}
                className={cn(
                  "rounded-md px-3 py-1.5 text-sm transition-colors duration-150 ease-macos",
                  active
                    ? "bg-[var(--color-muted)] text-[var(--color-foreground)]"
                    : "text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)] hover:bg-[var(--color-muted)]"
                )}
              >
                {link.label}
              </Link>
            );
          })}
          {handle ? (
            <Link
              href={`/profile/${handle}`}
              className={cn(
                "rounded-md px-3 py-1.5 text-sm transition-colors duration-150 ease-macos",
                pathname?.startsWith(`/profile/${handle}`)
                  ? "bg-[var(--color-muted)] text-[var(--color-foreground)]"
                  : "text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)] hover:bg-[var(--color-muted)]"
              )}
            >
              Profile
            </Link>
          ) : null}
        </nav>

        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            size="icon"
            aria-label={
              resolvedTheme === "dark"
                ? "Switch to light theme"
                : "Switch to dark theme"
            }
            onClick={toggle}
          >
            {mounted ? (
              resolvedTheme === "dark" ? (
                <Sun className="size-4" />
              ) : (
                <Moon className="size-4" />
              )
            ) : (
              <Sun className="size-4 opacity-0" />
            )}
          </Button>
          {meQuery.isLoading ? (
            <Skeleton className="h-8 w-24 rounded-md" />
          ) : user ? (
            <>
              {handle ? (
                <Link
                  href={`/profile/${handle}`}
                  className="inline-flex items-center gap-1.5 rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-2.5 py-1 text-xs font-medium text-[var(--color-foreground)] transition-colors duration-150 ease-macos hover:bg-[var(--color-muted)]"
                  data-testid="header-handle"
                >
                  <UserIcon className="size-3.5" aria-hidden />
                  <span>
                    <span className="text-[var(--color-muted-foreground)]">@</span>
                    {handle}
                  </span>
                </Link>
              ) : null}
              {/* P0-5 — direct links to account self-service and the
                  privacy / consent tab. Plain Button rows to preserve the
                  existing header rhythm; the dedicated dropdown primitive
                  is intentionally not used here. */}
              <Button
                asChild
                size="sm"
                variant="ghost"
                aria-label="Open account settings"
                data-testid="header-account"
              >
                <Link href="/account">
                  <Settings className="size-3.5" aria-hidden />
                  Account
                </Link>
              </Button>
              <Button
                asChild
                size="sm"
                variant="ghost"
                aria-label="Open privacy settings"
                data-testid="header-privacy"
              >
                <Link href="/account/privacy">
                  <Shield className="size-3.5" aria-hidden />
                  Privacy
                </Link>
              </Button>
              {user.tutorial_completed_at ? (
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => replayTutorialMutation.mutate()}
                  disabled={replayTutorialMutation.isPending}
                  aria-label="Replay tutorial"
                  data-testid="header-replay-tutorial"
                  title="Replay orientation tutorial"
                >
                  <RotateCcw className="size-3.5" aria-hidden />
                  Replay tutorial
                </Button>
              ) : null}
              <Button
                size="sm"
                variant="ghost"
                onClick={() => logoutMutation.mutate()}
                disabled={logoutMutation.isPending}
                aria-label="Sign out"
              >
                <LogOut className="size-3.5" aria-hidden />
                Sign out
              </Button>
            </>
          ) : showCta ? (
            <Button asChild size="sm">
              <Link href="/auth/sign-in">Sign in</Link>
            </Button>
          ) : null}
        </div>
      </div>
    </header>
  );
}
