import type { Metadata } from "next";
import { LegalShell } from "../_components/LegalShell";

export const metadata: Metadata = {
  title: "Terms of service",
  description:
    "The terms governing your use of OpenAgentDojo's sandboxed agent supervision training platform.",
};

export default function TermsPage() {
  return (
    <LegalShell slug="terms of service" title="Terms of service">
      <p>
        These Terms of Service (the &ldquo;Terms&rdquo;) govern your access to
        and use of OpenAgentDojo (the &ldquo;Service&rdquo;), a browser-based
        training environment for supervising AI coding agents inside
        sandboxed code repositories. By creating an account or otherwise
        accessing the Service you agree to be bound by these Terms.
      </p>

      <h2>1. The service</h2>
      <p>
        OpenAgentDojo provides time-boxed, single-tenant sandboxes in which
        you supervise a deliberately-flawed AI coding agent against a
        scripted mission. Your prompts, the agent&rsquo;s responses, the
        commands you run, and the patches you apply are recorded as a
        supervision timeline that is graded against a deterministic
        seven-dimension rubric.
      </p>

      <h2>2. Accounts</h2>
      <p>
        You sign in with an email address using a magic link. You are
        responsible for keeping that mailbox secure. You may not share an
        account or use the Service through automated means without our
        prior written consent. We reserve the right to suspend or terminate
        any account that we reasonably believe is being used in violation
        of these Terms.
      </p>

      <h2>3. Permitted use</h2>
      <p>
        The Service is provided for individual learning, training, and
        evaluation of AI-supervision skills. You may use the sandbox to
        complete missions, review the resulting reports, and reference your
        submission history. You may share read-only report links generated
        through the Service.
      </p>

      <h2>4. Prohibited workloads</h2>
      <p>
        The sandbox is not a general-purpose compute environment. You agree
        not to use the Service to:
      </p>
      <ul>
        <li>
          run malicious workloads, including network scanners, denial-of-
          service tools, cryptocurrency miners, or any code intended to
          compromise systems you do not own;
        </li>
        <li>
          attempt to escape the sandbox, exfiltrate other users&rsquo; data,
          or interfere with the operation of the underlying infrastructure;
        </li>
        <li>
          submit content that infringes third-party intellectual property,
          violates applicable law, or contains personal data of others
          without a lawful basis;
        </li>
        <li>
          probe, scan, or test the vulnerability of the Service except
          through our coordinated disclosure process (see Section 11).
        </li>
      </ul>

      <h2>5. Fair-use rate limits</h2>
      <p>
        To keep the Service responsive for everyone, we enforce per-account
        rate limits on session creation, prompt submission, command
        execution, and submission grading. Limits are surfaced in-product
        via standard HTTP 429 responses with a <code>Retry-After</code>{" "}
        header. We may adjust limits without notice if we observe abusive
        or unusually expensive workloads.
      </p>

      <h2>6. Intellectual property</h2>
      <p>
        The Service, including all mission content, the rubric, the agent
        scaffolding, and the user interface, is owned by us and our
        licensors and is protected by intellectual-property law. We grant
        you a limited, non-exclusive, non-transferable, revocable license
        to use the Service in accordance with these Terms.
      </p>
      <p>
        You retain ownership of any code you write or paste into a sandbox.
        By submitting a session you grant us a worldwide, royalty-free
        license to store, process, display, and analyse that submission
        solely for the purposes of operating, securing, and improving the
        Service (including grading, generating reports, and producing
        aggregate, de-identified analytics).
      </p>

      <h2>7. IP retention and abuse prevention</h2>
      <p>
        We log the IP address you sign in from. The plain-text value is
        hashed within seven (7) days; the hash is retained for ninety (90)
        days to support rate-limit enforcement, fraud detection, and
        incident investigation. See the{" "}
        <a href="/legal/privacy">Privacy policy</a> for the full retention
        schedule.
      </p>

      <h2>8. Service availability</h2>
      <p>
        We provide the Service on an &ldquo;as is&rdquo; and &ldquo;as
        available&rdquo; basis. We do not guarantee that the Service will
        be uninterrupted, secure, or error-free, or that any sandbox or
        submission will be preserved indefinitely. Scheduled maintenance,
        outages, and changes to mission content may affect availability.
      </p>

      <h2>9. No warranty</h2>
      <p>
        To the maximum extent permitted by law, the Service is provided
        without warranties of any kind, whether express, implied, statutory,
        or otherwise, including but not limited to warranties of
        merchantability, fitness for a particular purpose, accuracy, and
        non-infringement. The grading rubric is a measurement of process
        quality, not a professional certification of skill.
      </p>

      <h2>10. Limitation of liability</h2>
      <p>
        To the maximum extent permitted by law, we are not liable for any
        indirect, incidental, special, consequential, or punitive damages,
        or for any loss of profits, revenues, data, goodwill, or other
        intangible losses, resulting from your use of (or inability to use)
        the Service. Our total cumulative liability for any claim arising
        out of or relating to these Terms or the Service is limited to one
        hundred US dollars (USD 100).
      </p>

      <h2>11. Security disclosure</h2>
      <p>
        If you believe you have discovered a security vulnerability in the
        Service, please report it to <code>security@openagentdojo.app</code>{" "}
        and allow us a reasonable window to investigate before any public
        disclosure. We will not pursue legal action against good-faith
        security researchers who comply with this process.
      </p>

      <h2>12. Termination</h2>
      <p>
        You may delete your account at any time from{" "}
        <a href="/account">Account</a>. The deletion request enters a
        seven-day grace period during which it can be cancelled; after the
        grace period your account and associated data are irreversibly
        purged, subject to limited retention required for legal compliance
        or fraud prevention.
      </p>

      <h2>13. Changes to these terms</h2>
      <p>
        We may revise these Terms from time to time. Material changes will
        be announced in-product and, where reasonably practicable, by
        email at least fourteen (14) days before they take effect.
        Continued use of the Service after the effective date constitutes
        acceptance of the revised Terms.
      </p>

      <h2>14. Governing law</h2>
      <p>
        These Terms are governed by the laws applicable at the registered
        seat of the operating entity, without regard to its conflict-of-law
        provisions. Nothing in these Terms limits any non-waivable rights
        you have as a consumer under the law of your country of residence.
      </p>

      <h2>15. Contact</h2>
      <p>
        Questions about these Terms can be sent to{" "}
        <code>legal@openagentdojo.app</code>.
      </p>
    </LegalShell>
  );
}
