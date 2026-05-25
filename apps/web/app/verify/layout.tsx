import type { Metadata } from "next";

/**
 * The verify route is a credentialing surface, not a marketing page.
 * No Header, no Footer, no analytics — every character on the page is
 * load-bearing. The wrapping ``app/layout.tsx`` still applies (providers,
 * cookie banner, theme tokens) so this layout adds nothing visual.
 */
export const metadata: Metadata = {
  title: "Verified report · OpenAgentDojo",
  description:
    "Verification surface for a graded OpenAgentDojo submission. The page renders a server-signed envelope that cannot be fabricated client-side.",
  robots: { index: true, follow: true },
};

export default function VerifyLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="min-h-dvh bg-[var(--color-background)]">{children}</div>
  );
}
