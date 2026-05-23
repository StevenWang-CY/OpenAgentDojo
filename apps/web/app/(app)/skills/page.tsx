import type { Metadata } from "next";
import { SkillsView } from "@/components/skills/SkillsView";

export const metadata: Metadata = {
  title: "Skills",
  description:
    "Per-failure-mode mastery — your attempt and pass count across every supervision skill the platform tests.",
};

export default function SkillsPage() {
  return <SkillsView />;
}
