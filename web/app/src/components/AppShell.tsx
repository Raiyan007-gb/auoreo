import { MenuIcon, MonitorIcon, SearchIcon } from "lucide-react";
import { useEffect, useState } from "react";
import { NavLink, Outlet } from "react-router";
import { CommandPalette } from "@/components/CommandPalette";
import { NAV_GROUPS, type NavItem, SETTINGS_NAV } from "@/components/nav";
import { usePendingApprovals } from "@/hooks/usePendingApprovals";
import { Button } from "@/components/ui/button";
import { Tip } from "@/components/ui/tooltip";
import { useControl } from "@/lib/controlContext";
import { cn } from "@/lib/utils";

const WS_LABEL: Record<string, string> = {
  open: "Control channel connected (WebSocket)",
  connecting: "Connecting control channel…",
  reconnecting: "Reconnecting control channel…",
  unavailable: "WebSocket unavailable — using REST fallbacks",
  closed: "Control channel closed",
};

const WS_SHORT: Record<string, string> = {
  open: "live",
  connecting: "connecting",
  reconnecting: "reconnecting",
  unavailable: "offline",
  closed: "closed",
};

export function AppShell() {
  const [palette, setPalette] = useState(false);
  const [navOpen, setNavOpen] = useState(false);
  const control = useControl();
  const pending = usePendingApprovals();
  const isLoopback = /^(127\.|localhost|\[::1\])/.test(window.location.hostname);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPalette((p) => !p);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div className="flex h-full">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:z-50 focus:rounded focus:bg-background focus:p-2"
      >
        Skip to content
      </a>
      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-40 flex w-60 shrink-0 flex-col border-r bg-sidebar transition-transform md:static md:translate-x-0",
          navOpen ? "translate-x-0" : "-translate-x-full",
        )}
        aria-label="Primary"
      >
        <div className="flex h-14 shrink-0 items-center gap-2.5 border-b px-4">
          <span aria-hidden className="inline-block size-4.5 rounded-full border-4 border-foreground" />
          <span className="text-[15px] font-bold tracking-tight">auoreo</span>
          <Tip label={WS_LABEL[control.state] ?? control.state} side="right">
            <span
              role="status"
              aria-label={WS_LABEL[control.state] ?? control.state}
              data-testid="ws-state"
              data-state={control.state}
              tabIndex={0}
              className="ml-auto inline-flex items-center gap-1.5 rounded-full border bg-card px-2 py-0.5 text-[11px] text-secondary-foreground"
            >
              <span
                aria-hidden
                className={cn(
                  "size-1.75 rounded-full",
                  control.state === "open" ? "bg-success" : control.state === "unavailable" ? "bg-muted-foreground/40" : "bg-warning animate-pulse",
                )}
              />
              {WS_SHORT[control.state] ?? control.state}
            </span>
          </Tip>
        </div>
        <nav className="flex flex-1 flex-col gap-3.5 overflow-y-auto px-2.5 py-3">
          {NAV_GROUPS.map((g) => (
            <div key={g.label} className="flex flex-col gap-0.5">
              <div className="px-2.5 pb-1 text-[11px] font-semibold tracking-[0.06em] text-muted-foreground uppercase">{g.label}</div>
              {g.items.map((n) => (
                <SideLink key={n.to} item={n} badge={n.badge === "approvals" ? pending : 0} onNavigate={() => setNavOpen(false)} />
              ))}
            </div>
          ))}
        </nav>
        <div className="flex flex-col gap-0.5 border-t p-2.5">
          <SideLink item={SETTINGS_NAV} onNavigate={() => setNavOpen(false)} />
          <button
            type="button"
            onClick={() => setPalette(true)}
            className="flex min-h-8 items-center gap-2.5 whitespace-nowrap rounded-lg px-2.5 text-left text-sm text-secondary-foreground hover:bg-accent"
          >
            <SearchIcon className="size-4" aria-hidden />
            <span className="flex-1">Search &amp; commands</span>
            <kbd className="rounded border bg-card px-1.5 font-mono text-[11px] text-muted-foreground">⌘K</kbd>
          </button>
          <div className="mt-1.5 flex items-center gap-2.5 rounded-[10px] border bg-card p-2.5">
            <span aria-hidden className="inline-flex size-7 items-center justify-center rounded-lg bg-brand-soft text-brand-ink">
              <MonitorIcon className="size-4" />
            </span>
            <div className="flex min-w-0 flex-col">
              <span className="text-[13px] font-medium">{isLoopback ? "This computer" : "Remote server"}</span>
              <span className="truncate font-mono text-[11px] text-muted-foreground">{window.location.host}</span>
            </div>
          </div>
        </div>
      </aside>
      {navOpen && <div className="fixed inset-0 z-30 bg-black/30 md:hidden" onClick={() => setNavOpen(false)} aria-hidden />}
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex h-12 items-center gap-2 border-b bg-card px-2 md:hidden">
          <Button variant="ghost" size="icon" aria-label="Open navigation" onClick={() => setNavOpen(true)}>
            <MenuIcon />
          </Button>
          <span aria-hidden className="inline-block size-4 rounded-full border-[3.5px] border-foreground" />
          <span className="font-bold tracking-tight">auoreo</span>
        </div>
        <main id="main" className="min-h-0 flex-1 overflow-auto">
          <Outlet />
        </main>
      </div>
      <CommandPalette open={palette} onOpenChange={setPalette} />
    </div>
  );
}

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: React.ReactNode;
  description?: React.ReactNode;
  actions?: React.ReactNode;
}) {
  return (
    <header className="flex flex-wrap items-end gap-x-4 gap-y-3 px-4 pt-6 pb-1 sm:px-8 sm:pt-7">
      <div className="min-w-[min(100%,280px)] flex-1">
        <h1 className="truncate text-[22px] font-semibold tracking-tight">{title}</h1>
        {description && <p className="mt-1 text-sm text-muted-foreground">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}

export function EmptyState({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-1 rounded-lg border border-dashed bg-card p-10 text-center">
      <p className="font-medium">{title}</p>
      {children && <div className="text-sm text-muted-foreground">{children}</div>}
    </div>
  );
}

export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  const msg = error instanceof Error ? error.message : String(error);
  return (
    <p role="alert" className="rounded-lg border border-destructive/30 bg-destructive-soft px-3 py-2 text-sm text-destructive-ink">
      {msg}
    </p>
  );
}

function SideLink({ item, badge = 0, onNavigate }: { item: NavItem; badge?: number; onNavigate: () => void }) {
  return (
    <NavLink
      to={item.to}
      onClick={onNavigate}
      className={({ isActive }) =>
        cn(
          "flex min-h-8 items-center gap-2.5 rounded-lg px-2.5 text-sm text-secondary-foreground hover:bg-accent hover:text-accent-foreground",
          isActive && "bg-accent font-semibold text-accent-foreground",
        )
      }
    >
      <item.icon className="size-4" aria-hidden />
      <span className="flex-1">{item.label}</span>
      {badge > 0 && (
        <span
          className="min-w-5 rounded-full bg-warning px-1.5 text-center text-[11px] leading-[18px] font-semibold text-card"
          aria-label={`${badge} waiting`}
          data-testid="nav-approvals-badge"
        >
          {badge}
        </span>
      )}
    </NavLink>
  );
}
