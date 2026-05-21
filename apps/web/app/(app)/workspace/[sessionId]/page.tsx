import type { Metadata } from "next";
import { WorkspaceShell } from "@/components/workspace/WorkspaceShell";

export const metadata: Metadata = {
  title: "Workspace",
  description: "Supervise the agent. Run checks. Submit when ready.",
};

interface PageProps {
  params: Promise<{ sessionId: string }>;
}

export default async function WorkspacePage({ params }: PageProps) {
  const { sessionId } = await params;
  return <WorkspaceShell sessionId={sessionId} />;
}
