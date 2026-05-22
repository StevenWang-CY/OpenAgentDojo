import Link from "next/link";
import { Compass, ExternalLink, Github } from "lucide-react";

// The status page is an in-app route at `/status` that proxies the API's
// `/status` JSON endpoint and renders it server-side. Using the internal
// route lets us keep the user inside our domain (and our theming) while still
// surfacing live backend health.
const STATUS_URL = "/status";

/**
 * Marketing footer for the public landing page. Distinct from the in-app
 * `<layout/Footer>` (which adapts to the signed-in user); this one is purely
 * content + outbound links.
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
      <div className="mx-auto grid max-w-6xl gap-8 px-6 py-12 sm:grid-cols-2 lg:grid-cols-4">
        <div>
          <Link
            href="/"
            className="inline-flex items-center gap-2 text-sm font-semibold tracking-tight"
          >
            <span
              aria-hidden
              className="grid size-7 place-items-center rounded-md bg-[var(--color-primary)] text-[var(--color-primary-foreground)] shadow-soft"
            >
              <Compass className="size-4" />
            </span>
            OpenAgentDojo
          </Link>
          <p className="mt-3 max-w-sm text-xs text-[var(--color-muted-foreground)]">
            A simulator for developers learning to supervise AI coding agents
            on real repositories. Built around process, not output.
          </p>
        </div>

        <FooterColumn title="Product">
          <FooterLink href="/missions">Missions</FooterLink>
          <FooterLink href="/auth/sign-in">Sign in</FooterLink>
        </FooterColumn>

        <FooterColumn title="Project">
          <FooterLink
            href="https://github.com/wangchuyue/realrepo-arena"
            external
          >
            <Github className="size-3.5" aria-hidden /> GitHub
            <ExternalLink className="size-3 opacity-60" aria-hidden />
          </FooterLink>
          <FooterLink href={STATUS_URL}>Status</FooterLink>
        </FooterColumn>
      </div>
      <div className="border-t border-[var(--color-border)]">
        <div className="mx-auto flex max-w-6xl flex-col gap-2 px-6 py-5 text-xs text-[var(--color-muted-foreground)] sm:flex-row sm:items-center sm:justify-between">
          <p>
            &copy; {new Date().getFullYear()} OpenAgentDojo. All rights
            reserved.
          </p>
          <p>
            Built with care to make AI supervision a learnable skill, not a
            vibe.
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
      <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-[var(--color-muted-foreground)]">
        {title}
      </p>
      <ul className="mt-3 space-y-2 text-sm">{children}</ul>
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
