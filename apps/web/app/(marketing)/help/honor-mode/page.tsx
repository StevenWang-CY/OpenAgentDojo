import type { Metadata } from "next";
import Link from "next/link";
import { LegalShell } from "../../legal/_components/LegalShell";

/**
 * P0-8 — public explainer for the Honor / Proctored posture switch.
 *
 * Linked from:
 *   - The workspace top-bar honor banner (``WorkspaceTopBar``).
 *   - The catalog Start dialog posture popover (``StartMissionButton``).
 *   - The verify page footer (when a viewer wants to understand what
 *     "honor mode attestation" vs. "verified report" actually mean).
 *
 * The page mirrors the structure of ``/help/signin`` so the visual
 * vocabulary of the help corner stays consistent: ``LegalShell`` for
 * the chrome, slug-cased ``h2`` anchors that are safe to share, no
 * client hydration. Operators copy these URLs into ticket replies.
 */
export const metadata: Metadata = {
  title: "Honor mode & proctored mode",
  description:
    "How OpenAgentDojo distinguishes honor-mode practice runs from proctored, verifiable credentials — and the integrity signals captured during proctored sessions.",
};

export default function HonorModeHelpPage() {
  return (
    <LegalShell slug="help · honor mode" title="Honor mode & proctored mode">
      <p>
        Every OpenAgentDojo session runs in one of two postures. The
        posture is fixed at session create and cannot be changed
        mid-mission — the choice determines what the resulting score
        means to a third party, and whether the browser is instrumented
        to detect tab-switching, large pastes, and similar integrity
        signals.
      </p>

      <h2 id="honor-mode">Honor mode (self-study)</h2>
      <p>
        Honor mode is the default. It is intended for learning and
        practice: your work is still recorded, graded, and visible on
        your private dashboard, but the resulting score is{" "}
        <strong>not a verified credential</strong>. No browser-level
        integrity signals are captured — you can tab away to consult
        documentation, paste freely from an AI assistant, and right-click
        without leaving an audit trail.
      </p>
      <p>
        Honor-mode submissions surface a{" "}
        <em>&ldquo;honor mode attestation&rdquo;</em> banner on the public
        verify page, and the report&rsquo;s social card carries the same
        eyebrow. The intent is to make the distinction obvious to anyone
        you share the link with — an honor-mode attempt cannot be
        misread as a verified credential.
      </p>

      <h2 id="proctored-mode">Proctored mode (verified)</h2>
      <p>
        Proctored mode opts the session into a verified-credential path.
        The score that results from a proctored attempt is the one that
        appears on your public profile&rsquo;s &ldquo;Verified only&rdquo;
        radar; the public verify page renders the full{" "}
        <em>&ldquo;verified report&rdquo;</em> chrome instead of the
        honor-mode attestation.
      </p>
      <p>
        Opting in is per-session and irrevocable: once you start a
        proctored attempt, switching back to honor mode requires
        abandoning the session. Choose the posture deliberately at the
        Start dialog.
      </p>

      <h2 id="verified-credential">How the verified credential is issued</h2>
      <p>
        When a proctored session is submitted, the grader stamps the
        resulting submission with{" "}
        <code>verified=true</code>. The verify endpoint
        (<code>GET /api/v1/verify/&#123;submission_id&#125;</code>) returns
        a server-signed envelope containing the submission id, the
        graded score, the rubric version, the integrity signal count,
        and a HMAC-signed verification hash. Anyone with the URL can
        independently verify the credential — the signature cannot be
        forged client-side.
      </p>

      <h2 id="integrity-signals">Integrity signals captured</h2>
      <p>
        Proctored mode attaches a small set of browser-level listeners
        for the duration of the session. The same listeners are{" "}
        <strong>not</strong> attached in honor mode. The full set:
      </p>
      <ul>
        <li>
          <strong>Tab blur / focus</strong>:{" "}
          <code>window.blur</code>, <code>window.focus</code>, and{" "}
          <code>document.visibilitychange</code>. The event payload
          carries the duration of visibility / blur so the audit log
          shows how long the browser was elsewhere.
        </li>
        <li>
          <strong>Large paste</strong>: <code>document.paste</code> with a
          payload above 200 characters. Shorter pastes — single-line
          variable names, short snippets — are intentionally ignored so
          the log doesn&rsquo;t carpet-bomb the timeline with noise.
        </li>
        <li>
          <strong>Right-click on paste targets</strong>:{" "}
          <code>document.contextmenu</code> events on the editor, agent
          chat, and terminal panes. Right-clicks elsewhere in the
          application chrome (toolbar, sidebar, navigation) are{" "}
          <em>not</em> recorded.
        </li>
      </ul>
      <p>
        Each kind is debounced to at most one event per 500ms so a
        rapid visibility flap (for example, macOS hover-to-preview)
        doesn&rsquo;t flood the audit log. A rolling counter is exposed
        on the workspace top bar so you can see how the session looks
        to a reviewer in real time.
      </p>

      <h2 id="limits">Limits of the system</h2>
      <p>
        Proctored mode is a <em>signal</em>, not a guarantee. Browser-level
        instrumentation cannot detect every form of cheating; a
        determined adversary running OpenAgentDojo in a second browser
        profile or on a second machine could circumvent the visibility
        listeners entirely. The signals are designed to be{" "}
        <em>useful evidence</em> for a reviewer, not a hermetic seal.
      </p>
      <p>
        We make no claim about identity verification beyond the
        integrity signals and the optional GitHub OAuth chip on your
        public profile. A &ldquo;verified credential&rdquo; is a record that
        the session was conducted under the proctored posture — the
        operator is responsible for any additional identity-binding
        their context requires.
      </p>

      <h2 id="related">Related</h2>
      <ul>
        <li>
          <Link href="/help/signin">Sign-in help</Link> — magic-link
          delivery, mail filters, and account recovery.
        </li>
        <li>
          <Link href="/legal/privacy">Privacy policy</Link> — the full
          list of data captured per session.
        </li>
      </ul>
    </LegalShell>
  );
}
