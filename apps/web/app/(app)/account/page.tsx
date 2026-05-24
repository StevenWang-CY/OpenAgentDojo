import type { Metadata } from "next";
import { AccountView } from "@/components/account/AccountView";

export const metadata: Metadata = {
  title: "Account · OpenAgentDojo",
  description:
    "Manage your profile, privacy preferences, exports, and account lifecycle.",
};

export default function AccountPage() {
  return <AccountView initialTab="profile" />;
}
