import { CommandIcon, MenuIcon } from "lucide-react";
import { useEffect, useState } from "react";
import { NavLink, Outlet } from "react-router";
import { CommandPalette } from "@/components/CommandPalette";
import { NAV } from "@/components/nav";
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
          "fixed inset-y-0 left-0 z-40 flex w-52 shrink-0 flex-col border-r bg-sidebar transition-transform md:static md:translate-x-0",
          navOpen ? "translate-x-0" : "-translate-x-full",
        )}
        aria-label="Primary"
      >
        <div className="flex h-12 items-center gap-2 border-b px-3">
          <span aria-hidden className="inline-block size-4 rounded-full border-[3px] border-foreground" />
          <span className="font-semibold tracking-tight">auoreo</span>
          <Tip label={WS_LABEL[control.state] ?? control.state} side="right">
            <span
              role="status"
              aria-label={WS_LABEL[control.state] ?? control.state}
              data-testid="ws-state"
              data-state={control.state}
              tabIndex={0}
              className="ml-auto inline-flex items-center gap-1.5 rounded-full border px-1.5 py-0.5 text-[10px] text-muted-foreground"
            >
              <span
                aria-hidden
                className={cn(
                  "size-2 rounded-full",
                  control.state === "open" ? "bg-success" : control.state === "unavailable" ? "bg-muted-foreground/40" : "bg-warning animate-pulse",
                )}
              />
              {WS_SHORT[control.state] ?? control.state}
            </span>
          </Tip>
        </div>
        <nav className="flex flex-1 flex-col gap-0.5 p-2">
          {NAV.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              onClick={() => setNavOpen(false)}
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-2 rounded-md px-2 py-1.5 text-sm text-muted-foreground hover:bg-accent hover:text-accent-foreground",
                  isActive && "bg-accent font-medium text-accent-foreground",
                )
              }
            >
              <n.icon className="size-4" aria-hidden />
              {n.label}
            </NavLink>
          ))}
        </nav>
        <div className="border-t p-2">
          <Button variant="ghost" size="sm" className="w-full justify-start text-muted-foreground" onClick={() => setPalette(true)}>
            <CommandIcon aria-hidden /> Command palette
            <kbd className="ml-auto rounded border px-1 font-mono text-[10px]">⌘K</kbd>
          </Button>
        </div>
      </aside>
      {navOpen && <div className="fixed inset-0 z-30 bg-black/30 md:hidden" onClick={() => setNavOpen(false)} aria-hidden />}
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex h-12 items-center border-b px-3 md:hidden">
          <Button variant="ghost" size="icon" aria-label="Open navigation" onClick={() => setNavOpen(true)}>
            <MenuIcon />
          </Button>
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
    <header className="flex flex-wrap items-center gap-3 border-b px-4 py-3">
      <div className="min-w-0 flex-1">
        <h1 className="truncate text-base font-semibold">{title}</h1>
        {description && <p className="text-xs text-muted-foreground">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}

export function EmptyState({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-1 rounded-lg border border-dashed p-8 text-center">
      <p className="font-medium">{title}</p>
      {children && <div className="text-sm text-muted-foreground">{children}</div>}
    </div>
  );
}

export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  const msg = error instanceof Error ? error.message : String(error);
  return (
    <p role="alert" className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
      {msg}
    </p>
  );
}
