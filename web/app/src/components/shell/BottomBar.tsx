import { InboxIcon, MessagesSquareIcon, SearchIcon, type LucideIcon } from "lucide-react";
import { NavLink } from "react-router";
import { cn } from "@/lib/utils";

const TAB =
  "relative flex flex-1 flex-col items-center justify-center gap-0.5 text-2xs font-medium text-muted-foreground transition-colors outline-none focus-visible:bg-surface-hover";

function Tab({ to, label, icon: Icon, badge = 0 }: { to: string; label: string; icon: LucideIcon; badge?: number }) {
  return (
    <NavLink to={to} className={({ isActive }) => cn(TAB, isActive && "text-foreground")}>
      {({ isActive }) => (
        <>
          <span className="relative">
            <Icon className={cn("size-5", isActive && "text-brand")} aria-hidden />
            {badge > 0 && (
              <span className="absolute -top-1 -right-2 min-w-4 rounded-full bg-warning px-1 text-center text-2xs leading-4 font-semibold text-card tabular-nums">
                {badge}
              </span>
            )}
          </span>
          {label}
        </>
      )}
    </NavLink>
  );
}

/** Phone-width tab bar: the three things you reach for most. Everything else is in the drawer (☰). */
export function BottomBar({ needs, onSearch }: { needs: number; onSearch: () => void }) {
  return (
    <nav
      aria-label="Quick navigation"
      data-testid="bottom-bar"
      className="fixed inset-x-0 bottom-0 z-30 flex h-14 border-t bg-card/95 pb-[env(safe-area-inset-bottom)] backdrop-blur md:hidden"
    >
      <Tab to="/sessions" label="Sessions" icon={MessagesSquareIcon} />
      <Tab to="/inbox" label="Inbox" icon={InboxIcon} badge={needs} />
      <button type="button" onClick={onSearch} className={TAB} aria-label="Search and commands">
        <SearchIcon className="size-5" aria-hidden />
        Search
      </button>
    </nav>
  );
}
