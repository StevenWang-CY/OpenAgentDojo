import type { Metadata } from "next";
import { LegalShell } from "../_components/LegalShell";

export const metadata: Metadata = {
  title: "Cookies",
  description:
    "The cookies and similar storage OpenAgentDojo uses, what each one does, and how long it lasts.",
};

export default function CookiesPage() {
  return (
    <LegalShell slug="cookies" title="Cookies and similar storage">
      <p>
        This page explains every cookie and browser-storage entry the
        Service writes to your device. Essential entries are required for
        sign-in and abuse prevention; analytics entries are only written
        after you grant consent through the cookie banner or{" "}
        <a href="/account/privacy">Account → Privacy</a>.
      </p>

      <h2>1. Categories</h2>
      <ul>
        <li>
          <strong>Functional.</strong> Required for the Service to work
          at all (authentication, CSRF defence, theme preferences,
          remembering your cookie choice). These cannot be opted out of
          because the Service would stop functioning.
        </li>
        <li>
          <strong>Analytics.</strong> Optional. Aggregate, anonymous
          product usage that helps us spot broken flows. Off by default;
          opt-in through the banner or{" "}
          <a href="/account/privacy">Account → Privacy</a>.
        </li>
        <li>
          <strong>Marketing.</strong> Reserved. We do not currently set
          any marketing cookies. If we do in the future, you will be asked
          to consent before they are written.
        </li>
      </ul>

      <h2>2. What we store</h2>
      <table>
        <thead>
          <tr>
            <th scope="col">Name</th>
            <th scope="col">Category</th>
            <th scope="col">Purpose</th>
            <th scope="col">Lifetime</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>
              <code>arena_session</code>
            </td>
            <td>Functional</td>
            <td>
              Signed, HTTP-only session cookie that proves you are signed
              in. Issued when you complete the magic-link sign-in flow.
            </td>
            <td>30 days (sliding)</td>
          </tr>
          <tr>
            <td>
              <code>arena_csrf</code>
            </td>
            <td>Functional</td>
            <td>
              Double-submit token paired with the session cookie. The
              client mirrors it on mutating requests to defeat CSRF
              attacks.
            </td>
            <td>Session (cleared when the browser closes)</td>
          </tr>
          <tr>
            <td>
              <code>consent_v</code>
            </td>
            <td>Functional</td>
            <td>
              Records your cookie-consent choices so the banner does not
              re-appear on every visit. Stored as a value in
              <em> localStorage</em>, not as a transmitted cookie; never
              leaves your browser.
            </td>
            <td>365 days (until cleared or policy bump)</td>
          </tr>
          <tr>
            <td>
              <code>theme</code>
            </td>
            <td>Functional</td>
            <td>
              Remembers whether you chose the light, dark, or system
              theme.
            </td>
            <td>Until cleared</td>
          </tr>
          <tr>
            <td>
              <code>_posthog</code> (and provider-equivalents)
            </td>
            <td>Analytics</td>
            <td>
              Set by our analytics provider when you grant analytics
              consent. Used to deduplicate route visits and produce
              anonymous usage metrics.
            </td>
            <td>Per provider (typically up to 13 months)</td>
          </tr>
        </tbody>
      </table>

      <h2>3. Managing your choices</h2>
      <p>
        You can change your cookie choices at any time:
      </p>
      <ul>
        <li>
          Open <a href="/account/privacy">Account → Privacy</a> and toggle
          each category individually.
        </li>
        <li>
          Clear <code>consent_v</code> from your browser&rsquo;s storage
          settings to re-trigger the banner on your next visit.
        </li>
        <li>
          Use your browser&rsquo;s built-in cookie controls to block or
          delete cookies. Note that blocking <code>arena_session</code> or{" "}
          <code>arena_csrf</code> will prevent you from signing in.
        </li>
      </ul>

      <h2>4. Third-party storage</h2>
      <p>
        When analytics is enabled, our provider may also use browser
        storage entries other than the ones listed above (e.g.{" "}
        <code>posthog_</code> prefixed local-storage keys). These are
        equivalent to cookies for the purposes of this disclosure and are
        only written after you grant analytics consent.
      </p>

      <h2>5. Changes</h2>
      <p>
        If we add, remove, or change the purpose of a cookie listed here,
        we will update this page and bump the cookie-policy version. The
        banner re-appears for everyone whose stored version is older than
        the current one so you can re-confirm your choice.
      </p>

      <h2>6. Contact</h2>
      <p>
        For questions about cookies and similar storage, contact{" "}
        <code>privacy@openagentdojo.app</code>.
      </p>
    </LegalShell>
  );
}
