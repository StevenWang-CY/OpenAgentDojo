import { Badge } from "@/components/ui/Badge";
import type { Difficulty } from "@arena/shared-types";

const COPY: Record<Difficulty, { label: string; tone: "success" | "warning" | "danger" }> = {
  beginner: { label: "Beginner", tone: "success" },
  intermediate: { label: "Intermediate", tone: "warning" },
  advanced: { label: "Advanced", tone: "danger" },
};

export function DifficultyBadge({ difficulty }: { difficulty: Difficulty }) {
  const { label, tone } = COPY[difficulty];
  return <Badge tone={tone}>{label}</Badge>;
}
