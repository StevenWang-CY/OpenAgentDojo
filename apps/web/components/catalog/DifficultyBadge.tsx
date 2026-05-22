import { Badge } from "@/components/ui/Badge";
import type { Difficulty } from "@arena/shared-types";
import { cn } from "@/lib/utils";

const COPY: Record<
  Difficulty,
  { label: string; tone: "success" | "warning" | "danger"; klass: string }
> = {
  beginner: {
    label: "beginner",
    tone: "success",
    klass: "text-[var(--color-success)]",
  },
  intermediate: {
    label: "intermediate",
    tone: "warning",
    klass: "text-[var(--color-warning)]",
  },
  advanced: {
    label: "advanced",
    tone: "danger",
    klass: "text-[var(--color-danger)]",
  },
};

interface DifficultyBadgeProps {
  difficulty: Difficulty;
  variant?: "word" | "pill";
  className?: string;
}

export function DifficultyBadge({
  difficulty,
  variant = "word",
  className,
}: DifficultyBadgeProps) {
  const { label, tone, klass } = COPY[difficulty];
  if (variant === "pill") {
    return (
      <Badge tone={tone} className={cn("capitalize", className)}>
        {label}
      </Badge>
    );
  }
  return (
    <span
      className={cn(
        "font-mono text-[10.5px] uppercase tracking-[0.06em]",
        klass,
        className,
      )}
    >
      {label}
    </span>
  );
}
