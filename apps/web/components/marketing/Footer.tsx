import Link from "next/link";
import { BrandMark } from "@/components/layout/BrandMark";

// The status page is an in-app route at `/status` that proxies the API's
// `/status` JSON endpoint and renders it server-side.
const STATUS_URL = "/status";
const GITHUB_URL = "https://github.com/StevenWang-CY/OpenAgentDojo";

/**
 * Marketing footer for the public landing page. Distinct from the in-app
 * `<layout/Footer>` (which adapts to the signed-in user). Adds a fourth
 * column ("Determinism") that surfaces the two technical promises the
 * project is built around — they are the actual product differentiator.
 */
export function MarketingFooter() {
  return (
    <footer
      aria-labelledby="marketing-footer-heading"
      className="border-t border-[var(--color-border)] bg-[var(--color-surface)]"
    >
      <h2 id="marketing-footer-heading" className="sr-only">
        Site footer
      </h2>
      <div className="mx-auto grid max-w-6xl gap-10 px-6 py-14 sm:grid-cols-2 lg:grid-cols-[1.6fr_1fr_1fr_1fr]">
        <div>
          <Link
            href="/"
            className="inline-flex items-center gap-2 text-sm font-semibold tracking-tight"
          >
            <BrandMark size={22} />
            OpenAgentDojo
          </Link>
          <p className="mt-3 max-w-[320px] text-pretty text-[13px] text-[var(--color-muted-foreground)]">
            A browser-based dojo for developers learning to supervise AI
            coding agents on real repositories. Built around process, not
            output.
          </p>
        </div>

        <FooterColumn title="Product">
          <FooterLink href="/missions">Missions</FooterLink>
          <FooterLink href="/auth/sign-in">Sign in</FooterLink>
        </FooterColumn>

        <FooterColumn title="Project">
          <FooterLink href={GITHUB_URL} external>
            GitHub ↗
          </FooterLink>
          <FooterLink href={STATUS_URL}>Status</FooterLink>
        </FooterColumn>

        <FooterColumn title="Determinism">
          <li className="text-[var(--color-muted-foreground)]">
            No LLM on the grading path
          </li>
          <li className="text-[var(--color-muted-foreground)]">
            Replays are byte-identical
          </li>
        </FooterColumn>
      </div>
      <div className="border-t border-[var(--color-border)]">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-6 py-5 text-xs text-[var(--color-muted-foreground)]">
          <p>
            &copy; {new Date().getFullYear()} OpenAgentDojo. All rights
            reserved.
          </p>
          <p className="font-mono">
            supervision is a learnable skill, not a vibe.
          </p>
        </div>
      </div>
    </footer>
  );
}

function FooterColumn({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <p className="font-mono text-[10px] font-semibold uppercase tracking-[0.18em] text-[var(--color-muted-foreground)]">
        {title}
      </p>
      <ul className="mt-3.5 space-y-2.5 text-sm">{children}</ul>
    </div>
  );
}

function FooterLink({
  href,
  external,
  children,
}: {
  href: string;
  external?: boolean;
  children: React.ReactNode;
}) {
  if (external) {
    return (
      <li>
        <a
          href={href}
          target="_blank"
          rel="noreferrer noopener"
          className="inline-flex items-center gap-1.5 text-[var(--color-muted-foreground)] transition-colors duration-150 ease-macos hover:text-[var(--color-foreground)]"
        >
          {children}
        </a>
      </li>
    );
  }
  return (
    <li>
      <Link
        href={href}
        className="text-[var(--color-muted-foreground)] transition-colors duration-150 ease-macos hover:text-[var(--color-foreground)]"
      >
        {children}
      </Link>
    </li>
  );
}
