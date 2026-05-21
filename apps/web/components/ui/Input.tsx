import * as React from "react";
import { cn } from "@/lib/utils";

export const Input = React.forwardRef<
  HTMLInputElement,
  React.InputHTMLAttributes<HTMLInputElement>
>(({ className, type = "text", ...props }, ref) => (
  <input
    ref={ref}
    type={type}
    className={cn(
      "h-9 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]",
      "px-3 py-2 text-sm text-[var(--color-foreground)]",
      "placeholder:text-[var(--color-muted-foreground)]",
      "transition-colors duration-150 ease-macos",
      "focus-visible:outline-none focus-visible:border-[var(--color-ring)] focus-visible:ring-2 focus-visible:ring-[var(--color-ring)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--color-background)]",
      "disabled:cursor-not-allowed disabled:opacity-50",
      className
    )}
    {...props}
  />
));
Input.displayName = "Input";
