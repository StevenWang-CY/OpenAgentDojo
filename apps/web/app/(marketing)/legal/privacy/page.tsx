import type { Metadata } from "next";
import { LegalShell } from "../_components/LegalShell";

export const metadata: Metadata = {
  title: "Privacy policy",
  description:
    "What OpenAgentDojo collects about you, why we collect it, who processes it on our behalf, and how to exercise your rights under GDPR, UK-DPA, CCPA/CPRA, PIPEDA, and LGPD.",
};

// NOTE: The data-protection contact must be configured per environment via
// ``NEXT_PUBLIC_DPO_EMAIL`` (or its server-side equivalent) before this
// policy is presented to real users. Until that variable is set, the
// fallback address below is the canonical inbox documented in CONTEXT.md.
const DPO_EMAIL = "privacy@openagentdojo.app";

export default function PrivacyPage() {
  return (
    <LegalShell slug="privacy policy" title="Privacy policy">
      <p>
        This Privacy Policy explains what personal data OpenAgentDojo
        collects about you, why we collect it, who processes it on our
        behalf, how long we keep it, and how you can exercise your rights.
        It is written to satisfy the GDPR (EU), UK-DPA (United Kingdom),
        CCPA/CPRA (California), PIPEDA (Canada), and LGPD (Brazil).
      </p>

      <h2>1. What we collect</h2>
      <p>
        We collect the following categories of personal data when you use
        the Service:
      </p>
      <ul>
        <li>
          <strong>Account identifiers.</strong> Your email address
          (required to deliver the magic-link sign-in), an optional display
          name, and an optional GitHub login when you connect your GitHub
          account.
        </li>
        <li>
          <strong>Behavioural telemetry.</strong> Within a session we
          record the prompts you send, the agent&rsquo;s responses, the
          commands you run, the patches you apply, the supervision events
          your actions generate, and the resulting grading report. These
          are tied to your account so that your profile and skills view
          reflect your real history.
        </li>
        <li>
          <strong>Operational metadata.</strong> Timestamps, sandbox
          identifiers, mission identifiers, score and rubric breakdowns,
          and the user-agent string of the browser you use.
        </li>
        <li>
          <strong>Network metadata.</strong> The IP address from which you
          sign in. The plain-text value is hashed within seven (7) days;
          only the hash is retained thereafter.
        </li>
        <li>
          <strong>Optional analytics.</strong> If you grant analytics
          consent through the cookie banner, our analytics provider sets
          its own identifiers in your browser and records the routes you
          visit. We do not enable this provider until you grant consent.
        </li>
        <li>
          <strong>Workspace scratchpad text (P1-4).</strong> Each mission
          session has a private scratchpad pane where you can write
          reasoning before prompting the agent. The text is stored
          alongside your session row and is visible only to you. When you
          choose to use the post-mortem coaching reflection feature, the
          text you typed in your scratchpad is sent to Anthropic via AWS
          Bedrock to generate the reflection. The reflection is cached
          (so the same notes always produce the same output) and persists
          until your account is deleted. You can opt out at any time via{" "}
          <a href="/account/privacy">Account → Privacy</a>; with the
          toggle off, the scratchpad still works locally and your text is
          never forwarded to Bedrock.
        </li>
      </ul>

      <h2>2. Why we collect it (legal bases)</h2>
      <ul>
        <li>
          <strong>To deliver the Service.</strong> Your email is the only
          way for us to send the magic-link that signs you in; your
          display name and handle drive how your profile renders to you
          and to anyone you share a report with. Legal basis: performance
          of a contract (GDPR Art. 6(1)(b)).
        </li>
        <li>
          <strong>To grade your submissions.</strong> The supervision
          events and graded reports are the product. Without them, the
          Service has no output. Legal basis: performance of a contract.
        </li>
        <li>
          <strong>To prevent abuse.</strong> IP addresses and operational
          metadata feed the rate-limit and fraud-prevention systems that
          keep the sandbox available for everyone. Legal basis: legitimate
          interest (GDPR Art. 6(1)(f)).
        </li>
        <li>
          <strong>To improve the Service.</strong> If you grant analytics
          consent, we record route-level usage to identify dead-ends and
          broken flows. Legal basis: consent (GDPR Art. 6(1)(a)),
          revocable at any time from{" "}
          <a href="/account/privacy">Account → Privacy</a>.
        </li>
      </ul>

      <h2>3. How long we keep it</h2>
      <ul>
        <li>
          <strong>Account and submission data.</strong> Retained for as
          long as your account is active. When you initiate deletion from
          Account, a seven-day grace period applies; after that, all
          submission and session data tied to your account is irreversibly
          purged, subject to any retention required by law.
        </li>
        <li>
          <strong>Application logs.</strong> Purged ninety (90) days after
          they are written.
        </li>
        <li>
          <strong>IP addresses.</strong> Plain-text values are replaced
          with salted SHA-256 hashes within seven (7) days. The hashed
          form is retained alongside other operational metadata for the
          ninety-day log window.
        </li>
        <li>
          <strong>Consent records.</strong> Each consent decision is
          stored as an immutable, append-only row keyed by your user id.
          Records are deleted when your account is deleted.
        </li>
      </ul>

      <h2>4. Sub-processors</h2>
      <p>
        We use the following sub-processors to operate the Service. Each
        is bound by a written data-processing agreement and processes
        personal data only on our documented instructions:
      </p>
      <ul>
        <li>
          <strong>Resend</strong> &mdash; delivery of magic-link sign-in
          emails.
        </li>
        <li>
          <strong>Amazon Web Services (AWS Bedrock)</strong> &mdash; we
          use AWS Bedrock (a managed-Anthropic hosting service operated
          by Amazon Web Services) to generate user-facing prose:
          next-mission recommendations, coaching reflections, and
          critical-moment summaries shown alongside your graded report.
          AWS Bedrock does not train on customer data. Each generated
          string is cached by content hash so the same inputs always
          return the same output, and the inputs are scoped tightly
          per-feature (see the surface-by-surface disclosures in §1).
          Bedrock is enabled by default for new accounts; you can opt out
          of the coaching reflection (the only surface that forwards
          scratchpad text) from{" "}
          <a href="/account/privacy">Account → Privacy</a>.
        </li>
        <li>
          <strong>Fly.io</strong> &mdash; application hosting and managed
          PostgreSQL for primary storage.
        </li>
        <li>
          <strong>Neon</strong> &mdash; managed PostgreSQL for
          development/preview deployments where used.
        </li>
        <li>
          <strong>Cloudflare R2</strong> &mdash; object storage for
          submission artifacts and exported data archives.
        </li>
        <li>
          <strong>Upstash Redis</strong> &mdash; background-job queue for
          grading, exports, and account deletion.
        </li>
      </ul>
      <p>
        We will update this list before adding any new sub-processor that
        will process your personal data.
      </p>

      <h2>5. International transfers</h2>
      <p>
        Some sub-processors operate outside your country of residence.
        When personal data is transferred out of the EEA, UK, or
        Switzerland, we rely on the Standard Contractual Clauses (SCCs)
        or, where applicable, the EU-US Data Privacy Framework.
      </p>

      <h2>6. Your rights</h2>
      <p>
        Subject to applicable law, you have the following rights with
        respect to your personal data. All of them are exercisable
        self-service through <a href="/account">Account</a>:
      </p>
      <ul>
        <li>
          <strong>Access (GDPR Art. 15 / CCPA &sect; 1798.110).</strong>{" "}
          Request a machine-readable archive of your account, submissions,
          and supervision events from{" "}
          <a href="/account">Account → Data</a>.
        </li>
        <li>
          <strong>Rectification (GDPR Art. 16).</strong> Edit your
          display name from <a href="/account">Account → Profile</a>;
          change your email from{" "}
          <a href="/account">Account → Profile → Change email</a>.
        </li>
        <li>
          <strong>Erasure (GDPR Art. 17 / CCPA &sect; 1798.105).</strong>{" "}
          Request account deletion from{" "}
          <a href="/account">Account → Danger zone</a>. Deletion runs on a
          seven-day grace timer that you can cancel during the grace
          period.
        </li>
        <li>
          <strong>Data portability (GDPR Art. 20).</strong> The data export
          archive is JSON and CSV so it can be re-used by any compatible
          tool.
        </li>
        <li>
          <strong>Restriction and objection (GDPR Arts. 18 &amp; 21).</strong>{" "}
          Contact <code>{DPO_EMAIL}</code> with your request.
        </li>
        <li>
          <strong>Withdrawal of consent.</strong> You can toggle analytics
          consent off at any time from{" "}
          <a href="/account/privacy">Account → Privacy</a>; the change is
          immediate.
        </li>
        <li>
          <strong>Right to lodge a complaint.</strong> If you are in the
          EEA, UK, or Switzerland, you have the right to complain to your
          local data-protection authority.
        </li>
      </ul>

      <h2>7. Cookies</h2>
      <p>
        We use a small set of essential cookies plus optional analytics
        cookies. The full list (purpose, lifetime, provider) is on the{" "}
        <a href="/legal/cookies">Cookies page</a>. Optional cookies are
        never set without your explicit consent.
      </p>

      <h2>8. Children</h2>
      <p>
        The Service is not directed to children under sixteen (16) and we
        do not knowingly collect personal data from them. If you believe a
        child has provided us with personal data, please contact us and we
        will delete it.
      </p>

      <h2>9. Changes to this policy</h2>
      <p>
        We will notify you in-product before any material change takes
        effect, and the cookie banner will re-appear so you can re-confirm
        any consent decisions affected by the change. The effective date
        at the top of this page reflects the current revision.
      </p>

      <h2>10. Share links and replay artefacts (P1-6)</h2>
      <p>
        Your share links include the report&rsquo;s events list. Prompt
        text and agent response text are <strong>redacted</strong> in
        share-link views &mdash; only the report owner can see the raw
        text in a downloaded replay bundle. Your scratchpad body is{" "}
        <strong>never</strong> included in share-link views. The replay
        artefact contents follow this matrix:
      </p>
      <ul>
        <li>
          <strong>Owner (signed in).</strong> Sees the full envelope, the
          full event stream including verbatim prompt text and agent
          responses, the final diff, and the scratchpad body.
        </li>
        <li>
          <strong>Share-link holder.</strong> Sees the envelope, the
          event stream with prompt and agent-response payloads replaced
          by a byte-count marker, and the final diff. The scratchpad
          body is omitted from the artefact entirely.
        </li>
        <li>
          <strong>Anonymous viewer (no cookie, no share token).</strong>{" "}
          Receives a 404 from the replay endpoint regardless of whether
          the underlying submission exists.
        </li>
      </ul>
      <p>
        The public verification page at <code>/verify/&lt;id&gt;</code>{" "}
        is separate from the replay endpoint and only exposes the
        verification envelope (score, mission, graded-at timestamp,
        signature) &mdash; it never includes prompt text, agent
        responses, the final diff, or the scratchpad body.
      </p>

      <h2>11. Contact &amp; data-protection officer</h2>
      <p>
        Our data-protection contact is{" "}
        <a href={`mailto:${DPO_EMAIL}`}>
          <code>{DPO_EMAIL}</code>
        </a>
        . For routine requests please use the self-service controls in{" "}
        <a href="/account">Account</a>; the inbox is monitored for matters
        that those controls do not cover (regulator inquiries, complex
        portability requests, etc.).
      </p>
    </LegalShell>
  );
}
