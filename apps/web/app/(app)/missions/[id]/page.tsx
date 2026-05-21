import { MissionDetailView } from "@/components/catalog/MissionDetailView";

interface PageProps {
  params: Promise<{ id: string }>;
}

export default async function MissionDetailPage({ params }: PageProps) {
  const { id } = await params;
  return (
    <div className="mx-auto max-w-5xl px-6 py-10">
      <MissionDetailView missionId={id} />
    </div>
  );
}
