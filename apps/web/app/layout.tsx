import type { Metadata, Viewport } from "next";
import { env } from "@/lib/env";
import { Providers } from "./providers";
import { CookieConsentBanner } from "@/components/legal/CookieConsentBanner";
import "./globals.css";

export const metadata: Metadata = {
  metadataBase: new URL(env.appUrl),
  title: {
    default: "OpenAgentDojo",
    template: "%s · OpenAgentDojo",
  },
  description:
    "Learn to supervise AI coding agents inside real repositories. OpenAgentDojo grades the process — not just the patch.",
  applicationName: "OpenAgentDojo",
  authors: [{ name: "OpenAgentDojo Team" }],
  openGraph: {
    title: "OpenAgentDojo",
    description:
      "A browser-based simulator that teaches developers to supervise AI coding agents inside real repositories.",
    type: "website",
  },
  twitter: {
    card: "summary_large_image",
    title: "OpenAgentDojo",
  },
  icons: {
    icon: [{ url: "/logo-mark.svg", type: "image/svg+xml" }],
    shortcut: [{ url: "/logo-mark.svg", type: "image/svg+xml" }],
    apple: [{ url: "/logo-mark.svg", type: "image/svg+xml" }],
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
        <Providers>
          {children}
          {/* P0-5 — global cookie banner. Mounted at the root so it's
              visible on every route (marketing, app, auth) without
              duplicate instances. The component renders nothing once the
              user has stored a choice. */}
          <CookieConsentBanner />
        </Providers>
      </body>
    </html>
  );
}
