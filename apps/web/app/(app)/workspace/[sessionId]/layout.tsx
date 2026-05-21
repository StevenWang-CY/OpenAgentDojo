// The workspace deliberately reuses the parent (app) layout's header but
// drops all internal padding so the panel grid can claim the full viewport.

export default function WorkspaceLayout({ children }: { children: React.ReactNode }) {
  return <div className="w-full">{children}</div>;
}
