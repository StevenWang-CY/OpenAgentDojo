import type { Metadata } from "next";
import { Hero } from "@/components/marketing/Hero";
import { HowItWorks } from "@/components/marketing/HowItWorks";
import { SampleReport } from "@/components/marketing/SampleReport";
import { ScenarioCarousel } from "@/components/marketing/ScenarioCarousel";

export const metadata: Metadata = {
  title: "Hello, OpenAgentDojo",
  description:
    "Learn to supervise AI coding agents inside real repositories. Pick a mission, prompt the agent, catch the wrong patch.",
};

export default function LandingPage() {
  return (
    <>
      <Hero />
      <HowItWorks />
      <ScenarioCarousel />
      <SampleReport />
    </>
  );
}
