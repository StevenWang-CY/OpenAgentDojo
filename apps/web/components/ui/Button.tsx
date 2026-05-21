"use client";

import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  [
    "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-lg",
    "text-sm font-medium leading-none select-none",
    "transition-[background-color,color,box-shadow,transform] duration-150 ease-macos",
    "active:scale-[0.98] motion-reduce:active:scale-100",
    "disabled:pointer-events-none disabled:opacity-50",
    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-ring)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--color-background)]",
    "[&_svg]:size-4 [&_svg]:shrink-0",
  ].join(" "),
  {
    variants: {
      variant: {
        primary:
          "bg-[var(--color-primary)] text-[var(--color-primary-foreground)] shadow-soft hover:brightness-110 active:brightness-95",
        secondary:
          "bg-[var(--color-surface-elevated)] text-[var(--color-foreground)] border border-[var(--color-border)] hover:bg-[var(--color-muted)]",
        ghost:
          "bg-transparent text-[var(--color-foreground)] hover:bg-[var(--color-muted)]",
        outline:
          "bg-transparent text-[var(--color-foreground)] border border-[var(--color-border-strong)] hover:bg-[var(--color-muted)]",
        destructive:
          "bg-[var(--color-danger)] text-white hover:brightness-110 active:brightness-95",
        link: "bg-transparent underline-offset-4 hover:underline text-[var(--color-primary)] px-0 py-0 h-auto",
      },
      size: {
        sm: "h-8 px-3 text-xs",
        md: "h-9 px-4",
        lg: "h-10 px-5 text-[15px]",
        icon: "h-9 w-9 p-0",
      },
    },
    defaultVariants: {
      variant: "primary",
      size: "md",
    },
  }
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return (
      <Comp
        ref={ref}
        className={cn(buttonVariants({ variant, size }), className)}
        {...props}
      />
    );
  }
);
Button.displayName = "Button";

export { buttonVariants };
