import type { SessionContext } from "@sdk/client";
import { Link } from "react-router";
import { Tip } from "@/components/ui/tooltip";
import { contextParts, pressureLevel, useSessionContext } from "@/lib/contextGauge";
import { cn } from "@/lib/utils";

const fmt = (n: number) => (n >= 10_000 ? `${(n / 1000).toFixed(1)}k` : n.toLocaleString());

/** Stacked occupancy bar: system / tools / messages ÷ context window (`ctx_viz`). */
export function ContextBar({ ctx, className }: { ctx: SessionContext; className?: string }) {
  const level = pressureLevel(ctx);
  return (
    <div
      role="meter"
      aria-label="Context window occupancy"
      aria-valuemin={0}
      aria-valuemax={ctx.context_window}
      aria-valuenow={ctx.pressure + ctx.tools_tokens}
      data-level={level}
      className={cn(
        "relative flex h-2 w-24 overflow-hidden rounded-full bg-muted ring-1 ring-transparent",
        level === 1 && "ring-warning",
        level === 2 && "ring-destructive",
        className,
      )}
    >
      {contextParts(ctx).map((p) => (
        <span key={p.key} className={p.cls} style={{ width: `${Math.min(100, p.pct)}%` }} />
      ))}
      <span
        aria-hidden
        className="absolute inset-y-0 w-px bg-warning"
        style={{ left: `${Math.min(100, (ctx.auto_compact_at / Math.max(1, ctx.context_window)) * 100)}%` }}
      />
    </div>
  );
}

export function ContextBreakdown({ ctx }: { ctx: SessionContext }) {
  return (
    <span className="flex flex-col gap-0.5" data-testid="ctx-tooltip">
      <span>
        {fmt(ctx.pressure + ctx.tools_tokens)} / {fmt(ctx.context_window)} tokens ({Math.round(ctx.ratio * 100)}%) · {ctx.model}
      </span>
      {contextParts(ctx).map((p) => (
        <span key={p.key}>
          {p.label}: {fmt(p.tokens)}
        </span>
      ))}
      <span>Auto-compaction at {fmt(ctx.auto_compact_at)}</span>
    </span>
  );
}

/** Usage page: context gauge for one session row. */
export function ContextCell({ sessionId }: { sessionId: string }) {
  const ctx = useSessionContext(sessionId, 0);
  if (!ctx) return <span className="text-xs text-muted-foreground">—</span>;
  return (
    <Tip label={<ContextBreakdown ctx={ctx} />}>
      <span tabIndex={0} className="flex items-center gap-1.5 text-xs" data-testid="usage-ctx" data-session={sessionId}>
        <ContextBar ctx={ctx} />
        <span className="font-mono tabular-nums">{Math.round(ctx.ratio * 100)}%</span>
      </span>
    </Tip>
  );
}

/** Session-header gauge; links to the Usage page. */
export function ContextGauge({ sessionId, refreshKey }: { sessionId: string; refreshKey: unknown }) {
  const ctx = useSessionContext(sessionId, refreshKey);
  if (!ctx) return null;
  return (
    <Tip label={<ContextBreakdown ctx={ctx} />}>
      <Link
        to="/usage"
        data-testid="ctx-gauge"
        className="flex items-center gap-1.5 rounded-md px-1 py-0.5 text-xs text-muted-foreground hover:bg-accent"
        aria-label={`Context ${Math.round(ctx.ratio * 100)}% of ${ctx.context_window} tokens`}
      >
        <ContextBar ctx={ctx} />
        <span className="font-mono tabular-nums">{Math.round(ctx.ratio * 100)}%</span>
      </Link>
    </Tip>
  );
}
