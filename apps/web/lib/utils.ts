import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/**
 * Merge Tailwind class strings safely — `clsx` handles conditional joining and
 * `twMerge` deduplicates conflicting utilities (e.g. `p-2` + `p-4` → `p-4`).
 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
