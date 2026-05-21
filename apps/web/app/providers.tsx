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
            refetchOnWindowFocus: false,
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
