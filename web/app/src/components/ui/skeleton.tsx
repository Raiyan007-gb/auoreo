import type * as React from "react";
import { cn } from "@/lib/utils";

/** Shimmering placeholder block; size it with classes (`h-4 w-40`). */
export function Skeleton({ className, ...props }: React.ComponentProps<"div">) {
  return <div aria-hidden data-slot="skeleton" className={cn("skeleton rounded-md", className)} {...props} />;
}

/** Placeholder rows for a list or table while it loads; screen readers hear one "Loading…". */
export function SkeletonRows({ rows = 4, label = "Loading…", className }: { rows?: number; label?: string; className?: string }) {
  return (
    <div role="status" aria-live="polite" className={cn("overflow-hidden rounded-xl border bg-card shadow-card", className)}>
      <span className="sr-only">{label}</span>
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="flex items-center gap-4 border-b border-border-subtle px-4 py-3.5 last:border-0">
          <Skeleton className="h-3.5 w-[min(16rem,40%)]" />
          <Skeleton className="h-3.5 flex-1 opacity-60" />
          <Skeleton className="h-5 w-14 rounded-full" />
        </div>
      ))}
    </div>
  );
}
