import { ChevronDownIcon, MonitorIcon, PanelLeftCloseIcon, PanelLeftOpenIcon, SearchIcon } from "lucide-react";
import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router";
import { CommandPalette } from "@/components/CommandPalette";
import { NAV_GROUPS, type NavItem, SETTINGS_NAV } from "@/components/nav";
import { BottomBar } from "@/components/shell/BottomBar";
import { BrandMark } from "@/components/shell/BrandMark";
import { TopBar } from "@/components/shell/TopBar";
import { useCollapsedGroups } from "@/components/shell/useCollapsedGroups";
import { useNeedsYou } from "@/hooks/useNeedsYou";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { Toaster } from "@/components/ui/toast";
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

const RAIL_KEY = "auoreo_nav_rail";

/** Is `to` the page `pathname` is on (`/session/:id` counts as Sessions)? */
function isHere(pathname: string, to: string): boolean {
  if (pathname === to || pathname.startsWith(`${to}/`)) return true;
  return to === "/sessions" && pathname.startsWith("/session/");
}

function loadRail(): boolean {
  try {
    return localStorage.getItem(RAIL_KEY) === "1";
  } catch {
    return false;
  }
}

function saveRail(on: boolean): void {
  try {
    if (on) localStorage.setItem(RAIL_KEY, "1");
    else localStorage.removeItem(RAIL_KEY);
  } catch {
    /* ignore */
  }
}

export function AppShell() {
  const [palette, setPalette] = useState(false);
  const [navOpen, setNavOpen] = useState(false);
  const [railPref, setRailPref] = useState(loadRail);
  const desktop = useMediaQuery("(min-width: 48rem)");
  const rail = railPref && desktop; // the mobile drawer always shows labels
  const control = useControl();
  const needs = useNeedsYou();
  const [folded, toggleGroup] = useCollapsedGroups();
  const { pathname } = useLocation();
  const isLoopback = /^(127\.|localhost|\[::1\])/.test(window.location.hostname);

  const toggleRail = () =>
    setRailPref((r) => {
      saveRail(!r);
      return !r;
    });

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

  const wsLabel = WS_LABEL[control.state] ?? control.state;
  const wsDot = cn(
    "size-1.75 shrink-0 rounded-full",
    control.state === "open"
      ? "bg-success shadow-[0_0_0_3px] shadow-success/20"
      : control.state === "unavailable"
        ? "bg-muted-foreground/40"
        : "bg-warning animate-pulse",
  );

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
          "fixed inset-y-0 left-0 z-40 flex shrink-0 flex-col border-r bg-sidebar transition-[transform,width] duration-200 ease-[cubic-bezier(0.23,1,0.32,1)] md:relative md:translate-x-0 motion-reduce:transition-none",
          rail ? "w-[60px]" : "w-60",
          navOpen ? "translate-x-0 shadow-raised" : "-translate-x-full",
        )}
        aria-label="Primary"
        data-rail={rail ? "true" : "false"}
      >
        <div className={cn("flex h-14 shrink-0 items-center", rail ? "justify-center px-0" : "gap-2.5 px-4")}>
          {rail ? (
            <Tip label="Expand sidebar" side="right">
              <button
                type="button"
                onClick={toggleRail}
                aria-label="Expand sidebar"
                className="rounded-[10px] outline-none transition-transform focus-visible:ring-2 focus-visible:ring-ring active:scale-95"
              >
                <BrandMark />
              </button>
            </Tip>
          ) : (
            <>
              <BrandMark />
              <span className="text-title font-semibold tracking-tight">auoreo</span>
            </>
          )}
          <Tip label={wsLabel} side="right">
            <span
              role="status"
              aria-label={wsLabel}
              data-testid="ws-state"
              data-state={control.state}
              tabIndex={0}
              className={cn(
                "inline-flex items-center gap-1.5 text-2xs text-secondary-foreground",
                rail
                  ? "absolute top-[38px] left-[38px] rounded-full bg-sidebar p-[3px]"
                  : "ml-auto rounded-full border bg-card px-2 py-0.5 shadow-card",
              )}
            >
              <span aria-hidden className={wsDot} />
              <span className={cn(rail && "sr-only")}>{WS_SHORT[control.state] ?? control.state}</span>
            </span>
          </Tip>
        </div>
        <nav className="scroll-quiet flex flex-1 flex-col gap-3 overflow-x-hidden overflow-y-auto px-2 py-2">
          {NAV_GROUPS.map((g) => {
            // A folded group still shows the page you are on, so the sidebar always says where you are.
            const open = rail || !folded.has(g.label);
            const items = open ? g.items : g.items.filter((n) => isHere(pathname, n.to));
            const hidden = open ? 0 : g.items.some((n) => n.badge === "approvals") ? needs.total : 0;
            return (
              <div key={g.label} role="group" aria-label={g.label} className="flex flex-col gap-px">
                {rail ? (
                  <div className="flex h-6 items-end justify-center pb-1.5">
                    <span aria-hidden className="h-px w-4 bg-border" />
                  </div>
                ) : (
                  <button
                    type="button"
                    onClick={() => toggleGroup(g.label)}
                    aria-expanded={open}
                    aria-label={`${g.label} section`}
                    data-testid="nav-group-toggle"
                    className="group/hd flex h-6 items-end gap-1 rounded-md px-2.5 pb-1.5 text-2xs font-medium tracking-[0.08em] whitespace-nowrap text-muted-foreground uppercase outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    {g.label}
                    {hidden > 0 && (
                      <span className="ml-1 rounded-full bg-warning px-1.5 text-2xs leading-4 font-semibold text-card tabular-nums normal-case">
                        {hidden}
                      </span>
                    )}
                    <ChevronDownIcon
                      aria-hidden
                      className={cn("ml-auto size-3 opacity-0 transition-[transform,opacity] group-hover/hd:opacity-100 group-focus-visible/hd:opacity-100", !open && "-rotate-90 opacity-100")}
                    />
                  </button>
                )}
                {items.map((n) => (
                  <SideLink
                    key={n.to}
                    item={n}
                    rail={rail}
                    badge={n.badge === "approvals" ? needs.total : 0}
                    onNavigate={() => setNavOpen(false)}
                  />
                ))}
              </div>
            );
          })}
        </nav>
        <div className="flex flex-col gap-px border-t p-2">
          <SideLink item={SETTINGS_NAV} rail={rail} onNavigate={() => setNavOpen(false)} />
          <RailTip on={rail} label="Search & commands (⌘K)">
            <button
              type="button"
              onClick={() => setPalette(true)}
              className={cn(
                "group/side flex min-h-8 items-center gap-2.5 rounded-[10px] px-2.5 text-left text-ui font-medium whitespace-nowrap text-foreground/70 transition-colors hover:bg-surface-hover hover:text-foreground",
                rail && "justify-center px-0",
              )}
            >
              <SearchIcon className="size-4 shrink-0 text-muted-foreground group-hover/side:text-foreground" aria-hidden />
              <span className={cn("flex-1", rail && "sr-only")}>Search &amp; commands</span>
              {!rail && (
                <kbd className="rounded-md border bg-card px-1.5 font-mono text-2xs text-muted-foreground shadow-card">⌘K</kbd>
              )}
            </button>
          </RailTip>
          <RailTip on={rail} label={`${isLoopback ? "This computer" : "Remote server"} · ${window.location.host}`}>
            <div
              className={cn(
                "mt-1.5 flex items-center gap-2.5 rounded-xl border bg-card shadow-card",
                rail ? "justify-center border-transparent bg-transparent p-1 shadow-none" : "p-2",
              )}
            >
              <span aria-hidden className="inline-flex size-7 shrink-0 items-center justify-center rounded-lg bg-brand-soft text-brand-ink">
                <MonitorIcon className="size-4" />
              </span>
              <div className={cn("flex min-w-0 flex-1 flex-col", rail && "sr-only")}>
                <span className="text-ui font-medium">{isLoopback ? "This computer" : "Remote server"}</span>
                <span className="truncate font-mono text-2xs text-muted-foreground">{window.location.host}</span>
              </div>
            </div>
          </RailTip>
          {desktop && (
            <button
              type="button"
              onClick={toggleRail}
              aria-label={rail ? "Expand sidebar" : "Collapse sidebar"}
              aria-expanded={!rail}
              className={cn(
                "mt-1 hidden min-h-7 items-center gap-2 rounded-lg px-2.5 text-xs text-muted-foreground transition-colors hover:bg-surface-hover hover:text-foreground md:flex",
                rail && "justify-center px-0",
              )}
            >
              {rail ? <PanelLeftOpenIcon className="size-4" aria-hidden /> : <PanelLeftCloseIcon className="size-4" aria-hidden />}
              {!rail && <span>Collapse</span>}
            </button>
          )}
        </div>
      </aside>
      {navOpen && (
        <div className="fixed inset-0 z-30 bg-black/30 backdrop-blur-[2px] md:hidden" onClick={() => setNavOpen(false)} aria-hidden />
      )}
      <div className="flex min-w-0 flex-1 flex-col max-md:pb-14">
        <TopBar needs={needs.total} onSearch={() => setPalette(true)} onMenu={() => setNavOpen(true)} />
        <main id="main" className="surface-glow min-h-0 flex-1 overflow-auto">
          <div key={pathname} className="animate-rise-in h-full">
            <Outlet />
          </div>
        </main>
      </div>
      <BottomBar needs={needs.total} onSearch={() => setPalette(true)} />
      <CommandPalette open={palette} onOpenChange={setPalette} />
      <Toaster />
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
    <header className="flex flex-wrap items-end gap-x-4 gap-y-3 page-gutter pt-6 pb-1 sm:pt-8">
      <div className="min-w-[min(100%,280px)] flex-1">
        <h1 className="truncate text-2xl font-semibold tracking-[-0.02em]">{title}</h1>
        {description && <p className="mt-1.5 max-w-2xl text-sm text-muted-foreground">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}

/** `action`: the next step, e.g. a "New session" button. */
export function EmptyState({ title, children, action }: { title: string; children?: React.ReactNode; action?: React.ReactNode }) {
  return (
    <div className="relative flex flex-col items-center justify-center gap-1.5 overflow-hidden rounded-xl border border-dashed bg-card/60 p-10 text-center">
      <span aria-hidden className="mb-1 inline-flex size-9 items-center justify-center rounded-full bg-brand-soft ring-4 ring-brand-soft/50">
        <span className="size-2 rounded-full bg-brand" />
      </span>
      <p className="font-medium tracking-tight">{title}</p>
      {children && <div className="max-w-md text-sm text-muted-foreground">{children}</div>}
      {action && <div className="mt-3">{action}</div>}
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

/** A tooltip only while the sidebar is an icon rail (labels are hidden then). */
function RailTip({ on, label, children }: { on: boolean; label: string; children: React.ReactElement }) {
  if (!on) return children;
  return (
    <Tip label={label} side="right">
      {children}
    </Tip>
  );
}

/** Nav row in the style of the 21st.dev "Sidebar" (wensity): soft selected pill, brand accent bar, quiet icons. */
function SideLink({ item, rail, badge = 0, onNavigate }: { item: NavItem; rail: boolean; badge?: number; onNavigate: () => void }) {
  return (
    <RailTip on={rail} label={badge > 0 ? `${item.label} (${badge} waiting)` : item.label}>
      <NavLink
        to={item.to}
        onClick={onNavigate}
        className={({ isActive }) =>
          cn(
            "group/side relative flex min-h-8 items-center gap-2.5 rounded-[10px] px-2.5 text-ui font-medium whitespace-nowrap outline-none transition-colors duration-150",
            "focus-visible:ring-2 focus-visible:ring-ring",
            rail && "justify-center px-0",
            isActive ? "bg-surface-selected text-foreground" : "text-foreground/70 hover:bg-surface-hover hover:text-foreground",
          )
        }
      >
        {({ isActive }) => (
          <>
            {isActive && (
              <span aria-hidden className="absolute top-1/2 left-0 h-4 w-[3px] -translate-x-2 -translate-y-1/2 rounded-full bg-brand" />
            )}
            <item.icon
              className={cn(
                "size-4 shrink-0 transition-colors",
                isActive ? "text-brand" : "text-muted-foreground group-hover/side:text-foreground",
              )}
              aria-hidden
            />
            <span className={cn("flex-1 truncate", rail && "sr-only")}>{item.label}</span>
            {badge > 0 && (
              <span
                className={cn(
                  "rounded-full bg-warning text-center text-2xs leading-[18px] font-semibold text-card tabular-nums",
                  rail ? "absolute top-0.5 right-1 min-w-4 px-1 text-2xs leading-4" : "min-w-5 px-1.5",
                )}
                aria-label={`${badge} waiting`}
                data-testid="nav-approvals-badge"
              >
                {badge}
              </span>
            )}
          </>
        )}
      </NavLink>
    </RailTip>
  );
}
