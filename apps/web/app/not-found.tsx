import Link from "next/link";
import { Button } from "@/components/ui/Button";

export default function NotFound() {
  return (
    <main className="flex min-h-dvh items-center justify-center px-6">
      <div className="max-w-md text-center">
        <p className="text-xs uppercase tracking-[0.2em] text-[var(--color-muted-foreground)]">
          404
        </p>
        <h1 className="mt-2 text-2xl font-semibold tracking-tight">
          We couldn&rsquo;t find that page.
        </h1>
        <p className="mt-3 text-sm text-[var(--color-muted-foreground)]">
          The mission, session, or report you&rsquo;re looking for may have
          been moved or never existed.
        </p>
        <div className="mt-6 flex items-center justify-center gap-3">
          <Button asChild>
            <Link href="/">Back to landing</Link>
          </Button>
          <Button asChild variant="secondary">
            <Link href="/missions">Browse missions</Link>
          </Button>
        </div>
      </div>
    </main>
  );
}
