"use client";

import * as React from "react";
import * as ProgressPrimitive from "@radix-ui/react-progress";
import { cn } from "@/lib/utils";

export interface ProgressProps
  extends React.ComponentPropsWithoutRef<typeof ProgressPrimitive.Root> {
  value?: number;
  /** 0–100. */
  max?: number;
}

export const Progress = React.forwardRef<
  React.ElementRef<typeof ProgressPrimitive.Root>,
  ProgressProps
>(({ className, value = 0, max = 100, ...props }, ref) => {
  const pct = Math.max(0, Math.min(100, (value / max) * 100));
  return (
    <ProgressPrimitive.Root
      ref={ref}
      className={cn(
        "relative h-2 w-full overflow-hidden rounded-full bg-[var(--color-muted)]",
        className
      )}
      {...props}
    >
      <ProgressPrimitive.Indicator
        className="size-full flex-1 bg-[var(--color-primary)] transition-transform duration-300 ease-macos"
        style={{ transform: `translateX(-${100 - pct}%)` }}
      />
    </ProgressPrimitive.Root>
  );
});
Progress.displayName = "Progress";
