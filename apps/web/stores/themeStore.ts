"use client";

import { useTheme as useNextTheme } from "next-themes";

/**
 * Thin wrapper around next-themes so the rest of the app imports themes from
 * a single, internal module. Keeps the door open to swap providers later
 * without touching every component.
 */
export interface ThemeApi {
  theme: "light" | "dark" | "system" | undefined;
  resolvedTheme: "light" | "dark" | undefined;
  setTheme(next: "light" | "dark" | "system"): void;
  toggle(): void;
}

export function useTheme(): ThemeApi {
  const { theme, resolvedTheme, setTheme } = useNextTheme();
  return {
    theme: theme as ThemeApi["theme"],
    resolvedTheme: resolvedTheme as ThemeApi["resolvedTheme"],
    setTheme,
    toggle() {
      setTheme(resolvedTheme === "dark" ? "light" : "dark");
    },
  };
}
