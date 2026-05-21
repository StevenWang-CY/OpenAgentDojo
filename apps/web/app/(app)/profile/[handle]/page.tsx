import type { Metadata } from "next";
import { ProfileView } from "@/components/profile/ProfileView";

interface PageProps {
  params: Promise<{ handle: string }>;
}

export async function generateMetadata({ params }: PageProps): Promise<Metadata> {
  const { handle } = await params;
  return {
    title: `@${handle} · Profile`,
    description: `Supervision missions, badges, and rubric averages for @${handle}.`,
  };
}

/**
 * Public profile route. `/profiles/{handle}` is open to unauthenticated
 * visitors per IMPLEMENTATION_PLAN §13.1.
 */
export default async function ProfilePage({ params }: PageProps) {
  const { handle } = await params;
  return <ProfileView handle={handle} />;
}
