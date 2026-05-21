"use client";

import Link from "next/link";
import { ArrowUpRight, Clock } from "lucide-react";
import type { Mission } from "@arena/shared-types";
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { DifficultyBadge } from "./DifficultyBadge";
import { formatEstimatedMinutes } from "@/lib/format";
import { track } from "@/lib/telemetry";

interface MissionCardProps {
  mission: Mission;
}

export function MissionCard({ mission }: MissionCardProps) {
  const href = `/missions/${mission.id}` as const;
  return (
    <Card className="group relative flex h-full flex-col transition-shadow duration-200 ease-macos hover:shadow-elevated focus-within:shadow-elevated">
      <CardHeader>
        <div className="flex items-start justify-between gap-2">
          <Badge tone="outline" className="font-mono text-[10px] tracking-normal">
            {mission.category}
          </Badge>
          <DifficultyBadge difficulty={mission.difficulty} />
        </div>
        <CardTitle className="mt-2">
          <Link
            href={href}
            onClick={() =>
              track("mission_viewed", {
                mission_id: mission.id,
                category: mission.category,
                difficulty: mission.difficulty,
                source: "catalog_card",
              })
            }
            className="after:absolute after:inset-0 after:content-[''] focus-visible:outline-none"
          >
            {mission.title}
          </Link>
        </CardTitle>
      </CardHeader>
      <CardContent className="flex-1 text-sm text-[var(--color-muted-foreground)]">
        <p className="line-clamp-3">{mission.short_description}</p>
      </CardContent>
      <CardFooter className="flex items-center justify-between border-t border-[var(--color-border)] pt-4">
        <div className="flex items-center gap-1.5 text-xs text-[var(--color-muted-foreground)]">
          <Clock className="size-3.5" aria-hidden />
          <span>{formatEstimatedMinutes(mission.estimated_minutes)}</span>
        </div>
        <div className="flex flex-wrap items-center justify-end gap-1.5">
          {mission.skills_tested.slice(0, 2).map((skill) => (
            <span
              key={skill}
              className="rounded-md bg-[var(--color-muted)] px-1.5 py-0.5 text-[10px] font-medium text-[var(--color-muted-foreground)]"
            >
              {skill}
            </span>
          ))}
          {(mission.skills_tested?.length ?? 0) > 2 ? (
            <span className="text-[10px] text-[var(--color-muted-foreground)]">
              +{(mission.skills_tested?.length ?? 0) - 2}
            </span>
          ) : null}
        </div>
      </CardFooter>
      <span
        aria-hidden
        className="pointer-events-none absolute right-4 top-4 text-[var(--color-muted-foreground)] opacity-0 transition-opacity duration-150 group-hover:opacity-100 group-focus-within:opacity-100"
      >
        <ArrowUpRight className="size-4" />
      </span>
    </Card>
  );
}
