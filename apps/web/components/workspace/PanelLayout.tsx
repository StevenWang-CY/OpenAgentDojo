"use client";

import * as React from "react";
import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";
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

/**
 * Implements the 6-pane layout from IMPLEMENTATION_PLAN.md §13.2 with
 * react-resizable-panels. All panel state is local — persistence happens via
 * the autoSaveId so layout survives reloads.
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
  return (
    <div className={cn("flex h-[calc(100dvh-3.5rem)] flex-col bg-[var(--color-background)]", className)}>
      <PanelGroup
        direction="vertical"
        autoSaveId={verticalKey}
        className="flex-1"
      >
        <Panel defaultSize={70} minSize={40}>
          <PanelGroup direction="horizontal" autoSaveId={horizontalKey}>
            <Panel defaultSize={20} minSize={14} maxSize={32}>
              <SurfaceFrame>{sidebar}</SurfaceFrame>
            </Panel>
            <VerticalHandle />
            <Panel defaultSize={55} minSize={30}>
              <SurfaceFrame>{editor}</SurfaceFrame>
            </Panel>
            <VerticalHandle />
            <Panel defaultSize={25} minSize={18} maxSize={40}>
              <SurfaceFrame>
                <TabbedColumn sections={rightTabs} groupLabel="Panels" />
              </SurfaceFrame>
            </Panel>
          </PanelGroup>
        </Panel>
        <HorizontalHandle />
        <Panel defaultSize={30} minSize={15}>
          <SurfaceFrame>
            <TabbedColumn sections={bottomTabs} groupLabel="Bottom panels" />
          </SurfaceFrame>
        </Panel>
      </PanelGroup>
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
  return <PanelResizeHandle className="w-1.5 cursor-col-resize" />;
}

function HorizontalHandle() {
  return <PanelResizeHandle className="h-1.5 cursor-row-resize" />;
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
