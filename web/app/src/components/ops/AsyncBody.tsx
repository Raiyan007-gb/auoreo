import { Loader2Icon, RotateCwIcon } from "lucide-react";
import type * as React from "react";
import { Link } from "react-router";
import { EmptyState, ErrorNote } from "@/components/AppShell";
import { OPS_STATUS } from "@/components/ops/opsUtils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { SkeletonRows } from "@/components/ui/skeleton";
import type { AsyncState } from "@/hooks/useAsync";
import { cn } from "@/lib/utils";

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <p role="status" aria-live="polite" className="flex items-center gap-2 p-4 text-sm text-muted-foreground">
      <Loader2Icon aria-hidden className="size-4 animate-spin" /> {label}
    </p>
  );
}

/** Loading → error (with retry) → empty → content, for one `useAsync` list. */
export function AsyncBody<T>({
  state,
  empty,
  emptyHint,
  isEmpty = (d) => Array.isArray(d) && d.length === 0,
  children,
}: {
  state: AsyncState<T>;
  empty: string;
  emptyHint?: React.ReactNode;
  isEmpty?: (d: T) => boolean;
  children: (data: T) => React.ReactNode;
}) {
  if (state.error && state.data === undefined)
    return (
      <div className="space-y-2">
        <ErrorNote error={state.error} />
        <Button variant="outline" size="sm" onClick={state.reload}>
          <RotateCwIcon aria-hidden /> Retry
        </Button>
      </div>
    );
  if (state.data === undefined) return <SkeletonRows rows={3} />;
  if (isEmpty(state.data)) return <EmptyState title={empty}>{emptyHint}</EmptyState>;
  return <>{children(state.data)}</>;
}

export function StatusBadge({ status, className }: { status: string; className?: string }) {
  return (
    <Badge variant={OPS_STATUS[status] ?? "outline"} className={className} data-status={status}>
      {status}
    </Badge>
  );
}

export function SessionLink({ id, className }: { id: string | null | undefined; className?: string }) {
  if (!id) return <span className="text-muted-foreground">—</span>;
  return (
    <Link to={`/session/${id}`} className={cn("font-mono text-xs text-primary underline-offset-2 hover:underline", className)}>
      {id}
    </Link>
  );
}

/** Small refresh icon button with an accessible name. */
export function RefreshButton({ onClick, busy, label = "Refresh" }: { onClick: () => void; busy?: boolean; label?: string }) {
  return (
    <Button variant="ghost" size="icon-sm" aria-label={label} title={label} onClick={onClick} disabled={busy}>
      <RotateCwIcon aria-hidden className={busy ? "animate-spin" : undefined} />
    </Button>
  );
}
