"use client";

import * as React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider } from "next-themes";
import { TooltipProvider } from "@/components/ui/Tooltip";
import { Toaster } from "@/components/ui/Toaster";
import { TelemetryProvider } from "@/components/TelemetryProvider";

interface ProvidersProps {
  children: React.ReactNode;
}

export function Providers({ children }: ProvidersProps) {
  // Persist the client across re-renders without recreating it.
  const [queryClient] = React.useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 30_000,
            gcTime: 5 * 60_000,
            // Refetch when the tab regains focus so the catalog / profile /
            // skills views auto-recover after a transient API outage (e.g.
            // backend restart, or the user fired up the API after opening
            // the page). Worst case the user pays one extra round-trip per
            // tab-switch — cheap compared to seeing a stale error state.
            refetchOnWindowFocus: true,
            retry: (failureCount, error) => {
              // Don't retry on auth / 4xx — only on network blips.
              const status =
                error && typeof error === "object" && "status" in error
                  ? (error as { status?: number }).status
                  : undefined;
              if (status && status >= 400 && status < 500) return false;
              return failureCount < 2;
            },
          },
        },
      })
  );

  // FE-audit fix — every ApiError with ``status===403 && code==="deletion_scheduled"``
  // (raised by the backend's DeletionLockMiddleware) dispatches a
  // ``deletion-lock-detected`` window event from ``lib/api.ts``. A stale tab
  // whose ``me.deletion_scheduled_at`` cache was cleared elsewhere would
  // otherwise show a generic toast. Invalidate ``["me"]`` here so the
  // ``DeletionLockBanner`` surfaces on the next render instead.
  React.useEffect(() => {
    if (typeof window === "undefined") return;
    const handler = () => {
      void queryClient.invalidateQueries({ queryKey: ["me"] });
    };
    window.addEventListener("deletion-lock-detected", handler);
    return () => {
      window.removeEventListener("deletion-lock-detected", handler);
    };
  }, [queryClient]);

  return (
    <ThemeProvider attribute="class" defaultTheme="system" enableSystem disableTransitionOnChange={false}>
      <QueryClientProvider client={queryClient}>
        <TooltipProvider delayDuration={200} skipDelayDuration={400}>
          <TelemetryProvider>
            {children}
            <Toaster />
          </TelemetryProvider>
        </TooltipProvider>
      </QueryClientProvider>
    </ThemeProvider>
  );
}
