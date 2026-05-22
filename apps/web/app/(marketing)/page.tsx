import type { Metadata } from "next";
import { Hero } from "@/components/marketing/Hero";
import { HowItWorks } from "@/components/marketing/HowItWorks";
import { SampleReport } from "@/components/marketing/SampleReport";
import { ScenarioCarousel } from "@/components/marketing/ScenarioCarousel";

export const metadata: Metadata = {
  title: "OpenAgentDojo — supervisor training",
  description:
    "Patches that look right, aren't. Train the eye that catches them. A dojo for developers learning to supervise AI coding agents on real repositories — graded deterministically on the process, not just the patch.",
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
