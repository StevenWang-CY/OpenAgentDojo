"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { LogOut, Moon, Sun, User as UserIcon } from "lucide-react";
import { toast } from "sonner";
import { ApiError, auth } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { useTheme } from "@/stores/themeStore";
import { cn } from "@/lib/utils";
import { BrandMark } from "./BrandMark";

interface HeaderProps {
  showCta?: boolean;
}

const NAV_LINKS = [{ href: "/missions", label: "Missions" }] as const;

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

  const logoutMutation = useMutation({
    mutationFn: () => auth.logout(),
    onSuccess() {
      toast.success("Signed out.");
      queryClient.setQueryData(["me"], undefined);
      void queryClient.invalidateQueries({ queryKey: ["me"] });
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
