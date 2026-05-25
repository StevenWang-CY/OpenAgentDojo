import type { Metadata } from "next";
import Link from "next/link";
import { LegalShell } from "../../legal/_components/LegalShell";

/**
 * P0-10 — static FAQ for users who didn't receive their magic-link
 * email. The marketing layout is intentionally re-used so the visual
 * vocabulary matches the surrounding /legal/* pages (clean, monospaced
 * section headings, dark/light theme aware via OKLCH tokens). The page
 * is linked from:
 *
 *   - The sign-in post-send card (``/auth/sign-in``).
 *   - The marketing footer (Sign-in help column entry).
 *   - The /help/signin canonical share URL — operators copy this into
 *     ticket replies when a user lands in support without finding it.
 *
 * Keep the section anchors stable (slug-cased h2 ids) so external
 * links into ``/help/signin#focused-folder`` etc. don't decay over time.
 */
export const metadata: Metadata = {
  title: "Sign-in help",
  description:
    "Troubleshoot magic-link delivery: spam filters, corporate mail servers, expired links, and the GitHub OAuth fallback.",
};

export default function SignInHelpPage() {
  return (
    <LegalShell slug="help · sign-in" title="Sign-in help">
      <p>
        OpenAgentDojo signs you in with a one-time magic link sent to
        your email address. The link expires in 30 minutes and can only
        be redeemed once. If the email never arrives, work through the
        sections below — most issues are one of a handful of mail
        delivery quirks.
      </p>

      <h2 id="missing-email">I didn&rsquo;t get the email</h2>
      <ol>
        <li>
          Wait sixty seconds. Mail servers occasionally batch deliveries
          and the link sometimes lands a beat later than you&rsquo;d
          expect.
        </li>
        <li>
          Search your <strong>spam</strong>, <strong>junk</strong>,{" "}
          <strong>promotions</strong>, <strong>focused</strong>, and{" "}
          <strong>other</strong> folders. Gmail, Outlook 365, and
          Apple Mail all route automated senders into different folders
          on first delivery.
        </li>
        <li>
          Add{" "}
          <code>hello@openagentdojo.app</code>{" "}
          (or whichever address your operator configured for{" "}
          <code>EMAIL_FROM</code>) to your contacts. Most providers
          will route future links to your inbox once the sender is
          known.
        </li>
        <li>
          Return to <Link href="/auth/sign-in">sign in</Link> and use
          the <em>Resend link</em> button to trigger a fresh delivery
          (one resend per minute is allowed).
        </li>
      </ol>

      <h2 id="corporate-mail">I&rsquo;m using a corporate mail server</h2>
      <p>
        Many corporate mail filters block automated senders by default —
        especially when the sending domain&rsquo;s SPF, DKIM, or DMARC
        records are not on the local allowlist. If your IT team is open
        to it, ask them to whitelist the sender domain configured by
        the OpenAgentDojo operator.
      </p>
      <p>
        If whitelisting isn&rsquo;t practical, try a personal email
        address instead. You can always change your account email
        later under{" "}
        <Link href="/account">Account → Email</Link>.
      </p>

      <h2 id="focused-folder">I&rsquo;m on Outlook 365 or Microsoft 365</h2>
      <p>
        Outlook 365 splits the inbox into a <strong>Focused</strong>{" "}
        and <strong>Other</strong> tab. Automated senders frequently
        land in <em>Other</em> on first contact. After opening the
        magic-link email, right-click and select{" "}
        <em>Move to Focused inbox → Always move to Focused</em> so
        future deliveries land where you expect them.
      </p>

      <h2 id="expired-link">The link expired</h2>
      <p>
        Magic links are deliberately short-lived: each link is valid
        for exactly thirty (30) minutes and is invalidated the moment
        it is consumed or replaced by a fresh request. If you click an
        expired link, return to{" "}
        <Link href="/auth/sign-in">sign in</Link> and request a new
        one. The old link cannot be revived — magic links are
        single-use credentials by design.
      </p>

      <h2 id="email-locked-out">I can&rsquo;t access my email</h2>
      <p>
        If you no longer have access to the email address on your
        OpenAgentDojo account — for example, you&rsquo;ve left the
        company whose mailbox you signed up with — you have two
        recovery paths:
      </p>
      <ul>
        <li>
          <strong>GitHub OAuth</strong>: if the operator has enabled
          the GitHub OAuth fallback, the sign-in page surfaces a{" "}
          <em>Continue with GitHub</em> button alongside the
          magic-link form. Provided your GitHub account&rsquo;s
          primary email matches the OpenAgentDojo account email, this
          path will sign you in without touching the inbox.
        </li>
        <li>
          <strong>Manual recovery</strong>: contact your operator
          (typically the address printed on the operator&rsquo;s{" "}
          <Link href="/legal/privacy">privacy policy</Link>) with a
          description of the account and any verifiable details.
          Recovery requests are reviewed manually.
        </li>
      </ul>

      <h2 id="contact">Still stuck?</h2>
      <p>
        Email <code>support@openagentdojo.app</code> with a short
        description of what you tried and, where possible, the
        approximate timestamp of your most recent sign-in attempt.
        Including the timestamp lets the operator correlate your
        request with the per-attempt delivery log on their side.
      </p>
    </LegalShell>
  );
}
