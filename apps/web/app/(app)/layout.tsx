import { Header } from "@/components/layout/Header";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-dvh flex-col">
      <Header showCta />
      <main id="main-content" className="flex-1">
        {children}
      </main>
    </div>
  );
}
