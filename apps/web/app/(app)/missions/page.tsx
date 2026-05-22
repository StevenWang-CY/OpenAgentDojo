import type { Metadata } from "next";
import { MissionGrid } from "@/components/catalog/MissionGrid";

export const metadata: Metadata = {
  title: "Missions",
  description:
    "Pick a supervision exercise. Each mission is a real repository with a deliberately-flawed agent.",
};

export default function MissionsPage() {
  return (
    <div className="mx-auto max-w-6xl px-6 py-14">
      <header>
        <p className="font-mono text-[11px] uppercase tracking-[0.18em] text-[var(--color-muted-foreground)]">
          <span className="text-[var(--color-primary)]">{"//"}</span> catalog
          · supervision missions
        </p>
        <h1 className="mt-1.5 text-3xl font-semibold tracking-tight">
          Curated supervision missions
        </h1>
        <p className="mt-2.5 max-w-2xl text-[var(--color-muted-foreground)]">
          Every mission ships with a real repo, a deliberately-flawed agent
          patch, hidden tests, and a 7-dimension rubric. Pick one to start.
        </p>
      </header>
      <div className="mt-7">
        <MissionGrid />
      </div>
    </div>
  );
}
