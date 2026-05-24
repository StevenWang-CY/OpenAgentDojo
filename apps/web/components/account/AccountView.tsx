"use client";

/**
 * P0-6 — Account self-service shell.
 *
 * Renders the tabbed surface at ``/account`` (and the ``/account/privacy``
 * deep-link). The tabs (Profile / Privacy / Data / Danger) persist via a
 * ``?tab=…`` URL search param so deep-links land on the right pane and the
 * browser back-button cycles through panes the user actually visited.
 *
 * Visual treatment per ``P0_DESIGN.md`` §P0-6:
 *   - section labels use the existing ``// ___`` mono motif (mirrors
 *     ``ReportView``'s ``<Section>`` helper).
 *   - Danger tab heading + delete button use destructive (red) tokens. Every
 *     other tab uses neutral / primary tokens.
 *   - The deletion-lock state is surfaced ABOVE the tabs via
 *     ``DeletionLockBanner`` — it explains why mutating actions elsewhere in
 *     the product will return 403 until the user cancels the deletion.
 */

import * as React from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { ApiError, auth } from "@/lib/api";
import { SectionLabel } from "@/components/ui/SectionLabel";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/Tabs";
import { Skeleton } from "@/components/ui/Skeleton";
import { DangerPanel } from "./DangerPanel";
import { DataExportPanel } from "./DataExportPanel";
import { DeletionLockBanner } from "./DeletionLockBanner";
import { PrivacyPanel } from "./PrivacyPanel";
import { ProfileForm } from "./ProfileForm";

// Re-export the shared label so existing call-sites under ``components/account/``
// keep importing ``SectionLabel`` from this module without churn.
export { SectionLabel };

export type AccountTab = "profile" | "privacy" | "data" | "danger";

const TAB_ORDER: AccountTab[] = ["profile", "privacy", "data", "danger"];

function isAccountTab(value: string | null): value is AccountTab {
  return value !== null && (TAB_ORDER as string[]).includes(value);
}

export interface AccountViewProps {
  /** Initial active tab. The ``/account/privacy`` route passes ``privacy``
   *  so the deep-link lands on the privacy pane without flashing Profile. */
  initialTab?: AccountTab;
}

export function AccountView({ initialTab = "profile" }: AccountViewProps) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  // The URL search param wins over ``initialTab`` so a deep-link like
  // ``/account?tab=danger`` lands on Danger even when the route component
  // defaults the prop. The Privacy route deliberately omits ``?tab`` so
  // ``initialTab='privacy'`` is the authoritative source.
  const paramTab = searchParams.get("tab");
  const activeTab: AccountTab = isAccountTab(paramTab) ? paramTab : initialTab;

  // Surface ``/me`` once — every panel reads off the same query so the
  // ``deletion_scheduled_at`` + ``pending_email`` state is consistent across
  // the page. Re-fetched on focus by the shared QueryClient config (30s
  // staleTime is fine — the panels invalidate after every successful
  // mutation).
  const meQuery = useQuery({
    queryKey: ["me"],
    queryFn: ({ signal }) => auth.me(signal),
    retry: (failureCount, error) => {
      if (error instanceof ApiError && (error.status === 401 || error.status === 0)) {
        return false;
      }
      return failureCount < 1;
    },
  });

  const handleTabChange = React.useCallback(
    (next: string) => {
      if (!isAccountTab(next) || next === activeTab) return;
      // The Privacy route owns its own URL — pushing ``?tab=privacy`` there
      // would create a redundant entry. Stay on the deep-link path and just
      // update the param when we're on ``/account``.
      const onPrivacyRoute = pathname === "/account/privacy";
      const targetPath = onPrivacyRoute && next === "privacy" ? "/account/privacy" : "/account";
      const params = new URLSearchParams();
      // Profile is the default; omit ``?tab=profile`` to keep URLs clean.
      if (next !== "profile" && !(onPrivacyRoute && next === "privacy")) {
        params.set("tab", next);
      }
      const search = params.toString();
      const href = search ? `${targetPath}?${search}` : targetPath;
      router.push(href);
    },
    [activeTab, pathname, router],
  );

  if (meQuery.isLoading) {
    return (
      <div className="mx-auto w-full max-w-3xl px-6 py-12" aria-busy>
        <Skeleton className="h-8 w-40" />
        <Skeleton className="mt-4 h-10 w-full" />
        <Skeleton className="mt-6 h-48 w-full" />
      </div>
    );
  }

  if (meQuery.isError || !meQuery.data) {
    return (
      <div className="mx-auto w-full max-w-3xl px-6 py-12">
        <SectionLabel>account</SectionLabel>
        <h1 className="mt-2 text-2xl font-semibold tracking-tight">
          You need to sign in
        </h1>
        <p
          className="mt-2 text-sm text-[var(--color-muted-foreground)]"
          role="alert"
        >
          We couldn&rsquo;t load your account. Sign in and try again.
        </p>
      </div>
    );
  }

  const me = meQuery.data;
  const locked = me.deletion_scheduled_at !== null;

  return (
    <div className="mx-auto w-full max-w-3xl px-6 py-10">
      <header className="mb-6">
        <SectionLabel>account</SectionLabel>
        <h1 className="mt-1 text-2xl font-semibold tracking-tight">
          Your account
        </h1>
        <p className="mt-1 text-sm text-[var(--color-muted-foreground)]">
          Manage your profile, privacy, exports, and account lifecycle.
        </p>
      </header>

      {locked ? <DeletionLockBanner scheduledFor={me.deletion_scheduled_at!} /> : null}

      <Tabs
        value={activeTab}
        onValueChange={handleTabChange}
        className="mt-2"
        data-testid="account-tabs"
      >
        {/* Wrap the TabsList in a horizontally-scrollable container so
            four triggers don't clip on narrow viewports (320px). The
            wrapper takes the scroll responsibility; the underlying
            primitive keeps its ``inline-flex`` layout and the active
            indicator continues to render correctly on desktop. */}
        <div className="-mx-1 overflow-x-auto px-1">
          <TabsList aria-label="Account sections" className="w-max">
            <TabsTrigger value="profile" data-testid="tab-profile">
              Profile
            </TabsTrigger>
            <TabsTrigger value="privacy" data-testid="tab-privacy">
              Privacy
            </TabsTrigger>
            <TabsTrigger value="data" data-testid="tab-data">
              Data
            </TabsTrigger>
            <TabsTrigger
              value="danger"
              data-testid="tab-danger"
              className="data-[state=active]:text-[var(--color-danger)]"
            >
              Danger
            </TabsTrigger>
          </TabsList>
        </div>

        <TabsContent value="profile" className="space-y-8" data-testid="panel-profile">
          <ProfileForm user={me} locked={locked} />
        </TabsContent>

        <TabsContent value="privacy" className="space-y-8" data-testid="panel-privacy">
          <PrivacyPanel />
        </TabsContent>

        <TabsContent value="data" className="space-y-8" data-testid="panel-data">
          <DataExportPanel locked={locked} />
        </TabsContent>

        <TabsContent value="danger" className="space-y-8" data-testid="panel-danger">
          <DangerPanel user={me} />
        </TabsContent>
      </Tabs>
    </div>
  );
}

