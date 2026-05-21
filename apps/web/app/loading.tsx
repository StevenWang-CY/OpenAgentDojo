import { Skeleton } from "@/components/ui/Skeleton";

export default function Loading() {
  return (
    <main className="mx-auto max-w-5xl px-6 py-16">
      <Skeleton className="h-9 w-2/3" />
      <Skeleton className="mt-3 h-4 w-1/2" />
      <div className="mt-10 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <Skeleton className="h-40" />
        <Skeleton className="h-40" />
        <Skeleton className="h-40" />
      </div>
    </main>
  );
}
