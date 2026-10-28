import { GaugeIcon, RotateCwIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useAsync } from "@/hooks/useAsync";
import { api } from "@/lib/api";
import { cn, fmtTokens } from "@/lib/utils";

const fmtBytes = (n: number) =>
  n >= 1 << 20 ? `${(n / (1 << 20)).toFixed(1)} MiB` : n >= 1024 ? `${(n / 1024).toFixed(1)} KiB` : `${n} B`;

/**
 * I5 Memory page gauge (spec §17 "compression gauge"): how much context compaction has
 * saved so far (1 − post/pre tokens over every `compaction.completed`) next to the size of
 * long-term memory (facts, chunks, transcript rows, memory.db).
 */
/** `refreshKey`: bump it after a write (e.g. a new fact) so the counts reload. */
export function MemoryUsageGauge({ refreshKey = 0 }: { refreshKey?: number }) {
  const usage = useAsync(() => api.memoryUsage(), [refreshKey]);
  const u = usage.data;
  const pct = u ? Math.round(u.saved_ratio * 100) : 0;
  return (
    <section className="flex flex-wrap items-center gap-4 rounded-lg border p-3" aria-label="Memory usage" data-testid="memory-usage">
      <GaugeIcon className="size-5 text-info" aria-hidden />
      <div className="min-w-48 flex-1 space-y-1">
        <div className="flex items-baseline justify-between text-sm">
          <span className="font-medium">Compaction savings</span>
          <span className="font-mono tabular-nums" data-testid="memory-compression-pct">
            {u ? `${pct}%` : "…"}
          </span>
        </div>
        <div
          role="meter"
          aria-label="Context saved by compaction"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={pct}
          data-testid="memory-compression"
          className="h-2 overflow-hidden rounded-full bg-muted"
        >
          <span className={cn("block h-full bg-success transition-all")} style={{ width: `${pct}%` }} />
        </div>
        <p className="text-xs text-muted-foreground">
          {u && u.compactions > 0
            ? `${u.compactions} compaction${u.compactions === 1 ? "" : "s"}: ${fmtTokens(u.pre_tokens)} → ${fmtTokens(u.post_tokens)} tokens, ${u.archived_messages} messages archived`
            : "No compactions yet"}
        </p>
      </div>
      <dl className="grid grid-cols-[auto_auto] gap-x-3 gap-y-0.5 text-xs" data-testid="memory-counts">
        <dt className="text-muted-foreground">Facts</dt>
        <dd className="font-mono tabular-nums">{u?.facts ?? "—"}</dd>
        <dt className="text-muted-foreground">Chunks</dt>
        <dd className="font-mono tabular-nums">{u?.chunks ?? "—"}</dd>
        <dt className="text-muted-foreground">Transcript</dt>
        <dd className="font-mono tabular-nums">{u?.transcript ?? "—"}</dd>
        <dt className="text-muted-foreground">memory.db</dt>
        <dd className="font-mono tabular-nums">{u ? fmtBytes(u.db_bytes) : "—"}</dd>
      </dl>
      <Button variant="ghost" size="icon-sm" aria-label="Refresh memory usage" onClick={usage.reload}>
        <RotateCwIcon />
      </Button>
      {u && !u.enabled && <p className="w-full text-xs text-warning">Long-term memory is disabled on this server.</p>}
    </section>
  );
}
