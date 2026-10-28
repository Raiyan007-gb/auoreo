import { InboxIcon, MenuIcon, SearchIcon } from "lucide-react";
import { Link, useLocation } from "react-router";
import { BrandMark } from "@/components/shell/BrandMark";
import { crumbsFor } from "@/components/shell/crumbs";
import { Button } from "@/components/ui/button";
import { Tip } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

/**
 * Top bar. md+: breadcrumbs on the left, search and the inbox count on the right. Below md it
 * is the app bar: menu, logo, current page, search, inbox (the sidebar is a drawer there).
 */
export function TopBar({ needs, onSearch, onMenu }: { needs: number; onSearch: () => void; onMenu: () => void }) {
  const { pathname } = useLocation();
  const crumbs = crumbsFor(pathname);
  const here = crumbs[crumbs.length - 1];
  return (
    <header className="flex h-12 shrink-0 items-center gap-2 border-b bg-card/80 px-2 backdrop-blur md:px-4" data-testid="topbar">
      <Button variant="ghost" size="icon" aria-label="Open navigation" className="md:hidden" onClick={onMenu}>
        <MenuIcon />
      </Button>
      <BrandMark className="size-6 md:hidden" />
      <nav aria-label="Breadcrumb" className="hidden min-w-0 md:block">
        <ol className="flex items-center gap-1.5 text-ui">
          {crumbs.map((c, i) => {
            const last = i === crumbs.length - 1;
            return (
              <li key={`${c.label}-${i}`} className="flex min-w-0 items-center gap-1.5">
                {i > 0 && (
                  <span aria-hidden className="text-muted-foreground/60">
                    /
                  </span>
                )}
                {c.to ? (
                  <Link to={c.to} className="truncate text-muted-foreground hover:text-foreground hover:underline">
                    {c.label}
                  </Link>
                ) : (
                  <span
                    aria-current={last ? "page" : undefined}
                    className={cn("truncate", last ? "font-medium text-foreground" : "text-muted-foreground", c.mono && "font-mono text-xs")}
                  >
                    {c.label}
                  </span>
                )}
              </li>
            );
          })}
        </ol>
      </nav>
      <span className="min-w-0 truncate font-semibold tracking-tight md:hidden">{here?.label ?? "auoreo"}</span>
      <div className="ml-auto flex items-center gap-1.5">
        <button
          type="button"
          onClick={onSearch}
          className="hidden h-8 w-64 items-center gap-2 rounded-lg border bg-card px-2.5 text-ui text-muted-foreground shadow-xs transition-colors hover:border-foreground/20 hover:text-foreground md:flex"
        >
          <SearchIcon className="size-4" aria-hidden />
          <span className="flex-1 text-left">Search…</span>
          <kbd aria-hidden className="rounded-md border bg-background px-1.5 font-mono text-2xs">
            ⌘K
          </kbd>
        </button>
        <Button variant="ghost" size="icon" aria-label="Open search and commands" className="md:hidden" onClick={onSearch}>
          <SearchIcon />
        </Button>
        <Tip label={needs > 0 ? `${needs} waiting on you` : "Inbox"}>
          <Button variant="ghost" size="icon" asChild className="relative">
            <Link to="/inbox" aria-label={needs > 0 ? `Inbox, ${needs} waiting` : "Inbox"}>
              <InboxIcon aria-hidden />
              {needs > 0 && (
                <span
                  data-testid="topbar-inbox-badge"
                  className="absolute -top-0.5 -right-0.5 min-w-4 rounded-full bg-warning px-1 text-center text-2xs leading-4 font-semibold text-card tabular-nums"
                >
                  {needs}
                </span>
              )}
            </Link>
          </Button>
        </Tip>
      </div>
    </header>
  );
}
