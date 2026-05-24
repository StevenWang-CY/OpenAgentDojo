import type { Metadata } from "next";
import { AccountView } from "@/components/account/AccountView";

export const metadata: Metadata = {
  title: "Privacy · Account · OpenAgentDojo",
  description:
    "Control what we track, where your audit trail lives, and how we treat cookies.",
};

export default function AccountPrivacyPage() {
  // The deep-link variant of /account that lands on the Privacy pane
  // without flashing the Profile tab first. Cookie banner "Manage" links
  // and the privacy policy page deep-link here directly.
  return <AccountView initialTab="privacy" />;
}
