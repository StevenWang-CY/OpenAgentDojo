"use client";

import { Toaster as SonnerToaster } from "sonner";
import { useTheme } from "@/stores/themeStore";

/**
 * Theme-aware toast surface. Mounted once at the root via providers.tsx.
 */
export function Toaster() {
  const { resolvedTheme } = useTheme();
  return (
    <SonnerToaster
      theme={resolvedTheme ?? "light"}
      position="bottom-right"
      richColors
      closeButton
      toastOptions={{
        classNames: {
          toast:
            "border border-[var(--color-border)] bg-[var(--color-surface)] text-[var(--color-foreground)] shadow-elevated rounded-lg",
          description: "text-[var(--color-muted-foreground)]",
        },
      }}
    />
  );
}
