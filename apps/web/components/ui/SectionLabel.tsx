import * as React from "react";
import { cn } from "@/lib/utils";

/**
 * The ``// caption`` mono-uppercase motif used as a section label across the
 * report, account, and legal surfaces. Canonical spec lives here so the
 * label can't drift between surfaces (the audit caught three diverging
 * implementations: ``text-[11px] font-semibold`` in ``ReportView``,
 * ``text-[10.5px]`` in ``AccountView``, and ``font-mono text-xs`` in
 * ``LegalShell``).
 *
 * Visual: ``// SLUG`` — the slashes pick up the primary token, the slug
 * picks up the muted-foreground token. The component renders a ``<p>`` by
 * default to match the existing call-sites; pass ``as="h2"`` (etc.) at the
 * call-site by composing if a semantic heading is required.
 */
export interface SectionLabelProps
  extends Omit<React.HTMLAttributes<HTMLParagraphElement>, "children"> {
  children: React.ReactNode;
}

export function SectionLabel({
  children,
  className,
  ...rest
}: SectionLabelProps) {
  return (
    <p
      className={cn(
        "font-mono text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--color-muted-foreground)]",
        className,
      )}
      {...rest}
    >
      <span className="text-[var(--color-primary)]">{"//"}</span> {children}
    </p>
  );
}
