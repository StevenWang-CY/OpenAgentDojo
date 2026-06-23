"use client";

import * as React from "react";
import {
  Group,
  Panel,
  Separator,
  useDefaultLayout,
  type LayoutStorage,
} from "react-resizable-panels";
import { cn } from "@/lib/utils";

interface PanelSection {
  id: string;
  label: string;
  content: React.ReactNode;
}

export interface PanelLayoutProps {
  /** Left sidebar — file tree + context selector. */
  sidebar: React.ReactNode;
  /** Main editor / diff column. */
  editor: React.ReactNode;
  /** Right column tabs: brief, signals, chat. */
  rightTabs: PanelSection[];
  /** Bottom tabs: terminal, tests, timeline. */
  bottomTabs: PanelSection[];
  /**
   * Mission id — keys panel-size persistence. Per IMPLEMENTATION_PLAN §13.2,
   * layout state survives reloads on a *per-mission* basis. Optional so tests
   * and stories can mount the layout in isolation.
   */
  missionId?: string;
  className?: string;
}

// react-resizable-panels v4 persists layout via the `useDefaultLayout` hook,
// whose default storage is `localStorage`. That reference is evaluated during
// the hook body, which would throw under Next.js server rendering (this is a
// client component but is still SSR'd for the initial HTML). Wrap localStorage
// so reads/writes no-op on the server and during access failures.
const safeLayoutStorage: LayoutStorage = {
  getItem(key) {
    if (typeof window === "undefined") return null;
    try {
      return window.localStorage.getItem(key);
    } catch {
      return null;
    }
  },
  setItem(key, value) {
    if (typeof window === "undefined") return;
    try {
      window.localStorage.setItem(key, value);
    } catch {
      /* storage may be unavailable (private mode, quota) — ignore. */
    }
  },
};

/**
 * Implements the 6-pane layout from IMPLEMENTATION_PLAN.md §13.2 with
 * react-resizable-panels. All panel state is local — persistence happens via
 * `useDefaultLayout` so layout survives reloads on a per-mission basis.
 */
export function PanelLayout({
  sidebar,
  editor,
  rightTabs,
  bottomTabs,
  missionId,
  className,
}: PanelLayoutProps) {
  const verticalKey = missionId
    ? `arena-workspace-vertical:${missionId}`
    : "arena-workspace-vertical";
  const horizontalKey = missionId
    ? `arena-workspace-horizontal:${missionId}`
    : "arena-workspace-horizontal";

  const vertical = useDefaultLayout({ id: verticalKey, storage: safeLayoutStorage });
  const horizontal = useDefaultLayout({ id: horizontalKey, storage: safeLayoutStorage });

  return (
    <div className={cn("flex h-[calc(100dvh-3.5rem)] flex-col bg-[var(--color-background)]", className)}>
      <Group
        orientation="vertical"
        id={verticalKey}
        defaultLayout={vertical.defaultLayout}
        onLayoutChanged={vertical.onLayoutChanged}
        className="flex-1"
      >
        <Panel id="main" defaultSize="70%" minSize="40%">
          <Group
            orientation="horizontal"
            id={horizontalKey}
            defaultLayout={horizontal.defaultLayout}
            onLayoutChanged={horizontal.onLayoutChanged}
          >
            <Panel id="sidebar" defaultSize="20%" minSize="14%" maxSize="32%">
              <SurfaceFrame>{sidebar}</SurfaceFrame>
            </Panel>
            <VerticalHandle />
            <Panel id="editor" defaultSize="55%" minSize="30%">
              <SurfaceFrame>{editor}</SurfaceFrame>
            </Panel>
            <VerticalHandle />
            <Panel id="right" defaultSize="25%" minSize="18%" maxSize="40%">
              <SurfaceFrame>
                <TabbedColumn sections={rightTabs} groupLabel="Panels" />
              </SurfaceFrame>
            </Panel>
          </Group>
        </Panel>
        <HorizontalHandle />
        <Panel id="bottom" defaultSize="30%" minSize="15%">
          <SurfaceFrame>
            <TabbedColumn sections={bottomTabs} groupLabel="Bottom panels" />
          </SurfaceFrame>
        </Panel>
      </Group>
    </div>
  );
}

function SurfaceFrame({ children }: { children: React.ReactNode }) {
  return (
    <div className="h-full overflow-hidden border border-[var(--color-border)] bg-[var(--color-surface)]">
      {children}
    </div>
  );
}

// The handle is intentionally wider than its visible line — the line is
// painted by a centered ::after in globals.css (1px idle, 3px hover/active)
// so we get a generous hit area without a fat divider.
function VerticalHandle() {
  return <Separator className="w-1.5 cursor-col-resize" />;
}

function HorizontalHandle() {
  return <Separator className="h-1.5 cursor-row-resize" />;
}

function TabbedColumn({
  sections,
  groupLabel,
}: {
  sections: PanelSection[];
  groupLabel: string;
}) {
  const [activeId, setActiveId] = React.useState(sections[0]?.id ?? "");
  React.useEffect(() => {
    if (!sections.find((s) => s.id === activeId)) {
      setActiveId(sections[0]?.id ?? "");
    }
  }, [sections, activeId]);

  if (sections.length === 0) return null;

  return (
    <div className="flex h-full flex-col">
      <div
        role="tablist"
        aria-label={groupLabel}
        className="flex items-center gap-1 border-b border-[var(--color-border)] bg-[var(--color-surface-elevated)] px-2 py-1.5"
      >
        {sections.map((s) => {
          const active = s.id === activeId;
          return (
            <button
              key={s.id}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => setActiveId(s.id)}
              className={cn(
                "rounded-md px-2 py-0.5 text-[11px] font-medium uppercase tracking-wide",
                "transition-colors duration-150 ease-macos",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-ring)] focus-visible:ring-offset-1 focus-visible:ring-offset-[var(--color-surface-elevated)]",
                active
                  ? "bg-[var(--color-surface)] text-[var(--color-foreground)] shadow-soft"
                  : "text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)]"
              )}
            >
              {s.label}
            </button>
          );
        })}
      </div>
      <div className="flex-1 overflow-hidden">
        {sections.map((s) => (
          <div
            key={s.id}
            role="tabpanel"
            hidden={s.id !== activeId}
            className="h-full"
          >
            {s.id === activeId ? s.content : null}
          </div>
        ))}
      </div>
    </div>
  );
}
