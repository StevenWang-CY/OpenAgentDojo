import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[11px] font-medium leading-none uppercase tracking-wide border",
  {
    variants: {
      tone: {
        neutral:
          "bg-[var(--color-muted)] text-[var(--color-muted-foreground)] border-[var(--color-border)]",
        primary:
          "bg-[oklch(from_var(--color-primary)_l_c_h/0.15)] text-[var(--color-primary)] border-[oklch(from_var(--color-primary)_l_c_h/0.3)]",
        success:
          "bg-[oklch(from_var(--color-success)_l_c_h/0.15)] text-[var(--color-success)] border-[oklch(from_var(--color-success)_l_c_h/0.3)]",
        warning:
          "bg-[oklch(from_var(--color-warning)_l_c_h/0.18)] text-[var(--color-warning)] border-[oklch(from_var(--color-warning)_l_c_h/0.32)]",
        danger:
          "bg-[oklch(from_var(--color-danger)_l_c_h/0.15)] text-[var(--color-danger)] border-[oklch(from_var(--color-danger)_l_c_h/0.3)]",
        outline:
          "bg-transparent text-[var(--color-foreground)] border-[var(--color-border-strong)]",
      },
    },
    defaultVariants: { tone: "neutral" },
  }
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

export function Badge({ className, tone, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ tone }), className)} {...props} />;
}
