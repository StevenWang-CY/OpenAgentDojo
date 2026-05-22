import { cn } from "@/lib/utils";

/**
 * Typographic brand mark — dual-square dojo glyph used across the header and
 * footers. Pure CSS, no icon font. The outer square inherits ``foreground``
 * and the inner square is the ``primary`` accent.
 */
export function BrandMark({ className }: { className?: string }) {
  return (
    <span
      aria-hidden
      className={cn(
        "relative inline-block size-[14px] bg-[var(--color-foreground)] after:absolute after:inset-[3px] after:bg-[var(--color-primary)]",
        className
      )}
    />
  );
}
