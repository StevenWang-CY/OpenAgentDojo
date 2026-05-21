import type { Metadata, Viewport } from "next";
import { env } from "@/lib/env";
import { Providers } from "./providers";
import "./globals.css";

export const metadata: Metadata = {
  metadataBase: new URL(env.appUrl),
  title: {
    default: "AgentSupervisor Arena",
    template: "%s · AgentSupervisor Arena",
  },
  description:
    "Learn to supervise AI coding agents inside real repositories. AgentSupervisor Arena grades the process — not just the patch.",
  applicationName: "AgentSupervisor Arena",
  authors: [{ name: "Arena Team" }],
  openGraph: {
    title: "AgentSupervisor Arena",
    description:
      "A browser-based simulator that teaches developers to supervise AI coding agents inside real repositories.",
    type: "website",
  },
  twitter: {
    card: "summary_large_image",
    title: "AgentSupervisor Arena",
  },
  icons: {
    icon: [{ url: "/favicon.svg", type: "image/svg+xml" }],
  },
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#fafafa" },
    { media: "(prefers-color-scheme: dark)", color: "#0a0d12" },
  ],
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="min-h-dvh bg-[var(--color-background)] text-[var(--color-foreground)] antialiased">
        {/* Skip-to-content link — visible only when focused (a11y §13.6). */}
        <a href="#main-content" className="skip-to-content">
          Skip to content
        </a>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
