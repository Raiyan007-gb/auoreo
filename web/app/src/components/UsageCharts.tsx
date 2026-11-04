import type { SessionInfo, UsagePoint, UsageSeries } from "@sdk/client";
import { ShieldAlertIcon } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { cn, fmtTokens, fmtUsd } from "@/lib/utils";

const W = 320;
const H = 130;
const PAD = { l: 40, r: 4, t: 8, b: 18 };

function label(p: UsagePoint, bucket: string): string {
  const d = new Date(p.bucket);
  return bucket === "hour"
    ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : d.toLocaleDateString([], { month: "short", day: "numeric" });
}

/**
 * One measure over time as thin bars (single series → no legend; the title names it),
 * recessive grid, hover readout per bucket. Inline SVG: no chart library.
 */
function BarChart({
  title,
  points,
  bucket,
  value,
  fmt,
  testId,
}: {
  title: string;
  points: readonly UsagePoint[];
  bucket: string;
  value: (p: UsagePoint) => number;
  fmt: (n: number) => string;
  testId: string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const max = Math.max(...points.map(value), 0);
  const top = max > 0 ? max : 1;
  const iw = W - PAD.l - PAD.r;
  const ih = H - PAD.t - PAD.b;
  const slot = iw / Math.max(points.length, 1);
  const bar = Math.max(1, Math.min(24, slot - 2)); // 2px surface gap between bars
  const y = (v: number) => PAD.t + ih - (v / top) * ih;
  const every = Math.ceil(points.length / 4);
  const hovered = hover !== null ? points[hover] : undefined;
  return (
    <figure className="space-y-1" data-testid={testId}>
      <figcaption className="flex items-baseline justify-between text-sm font-medium">
        {title}
        <span className="text-xs font-normal text-muted-foreground tabular-nums" aria-live="polite">
          {hovered ? `${label(hovered, bucket)}: ${fmt(value(hovered))}` : `peak ${fmt(max)}`}
        </span>
      </figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img" aria-label={`${title} per ${bucket}`}>
        {[0, 0.5, 1].map((f) => (
          <g key={f}>
            <line x1={PAD.l} x2={W - PAD.r} y1={y(top * f)} y2={y(top * f)} className="stroke-border" strokeWidth={1} />
            <text x={PAD.l - 4} y={y(top * f) + 3} textAnchor="end" className="fill-muted-foreground text-[10px]">
              {fmt(top * f)}
            </text>
          </g>
        ))}
        {points.map((p, i) => {
          const v = value(p);
          const x = PAD.l + i * slot + (slot - bar) / 2;
          const h = Math.max(v > 0 ? 1 : 0, PAD.t + ih - y(v));
          return (
            <g key={p.bucket} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
              <rect x={PAD.l + i * slot} y={PAD.t} width={slot} height={ih} fill="transparent" />
              <rect
                x={x}
                y={PAD.t + ih - h}
                width={bar}
                height={h}
                rx={Math.min(4, bar / 2)}
                className={cn("fill-brand", hover !== null && hover !== i && "opacity-50")}
                data-testid="chart-bar"
              >
                <title>{`${label(p, bucket)}: ${fmt(v)}`}</title>
              </rect>
              {i % every === 0 && (
                <text x={PAD.l + i * slot + slot / 2} y={H - 6} textAnchor="middle" className="fill-muted-foreground text-[10px]">
                  {label(p, bucket)}
                </text>
              )}
            </g>
          );
        })}
      </svg>
    </figure>
  );
}

export function UsageCharts({ series }: { series: UsageSeries }) {
  return (
    <Card className="gap-2" data-testid="usage-charts">
      <CardHeader>
        <CardTitle className="text-base">Over time</CardTitle>
        <CardDescription>Per {series.bucket}, UTC. Hover a bar for its value; the tables below list exact totals.</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4 lg:grid-cols-2">
        <BarChart
          title="Tokens"
          testId="chart-tokens"
          points={series.points}
          bucket={series.bucket}
          value={(p) => p.prompt_tokens + p.completion_tokens}
          fmt={(n) => fmtTokens(Math.round(n))}
        />
        <BarChart
          title="Cached tokens"
          testId="chart-cached"
          points={series.points}
          bucket={series.bucket}
          value={(p) => p.cached_tokens ?? 0}
          fmt={(n) => fmtTokens(Math.round(n))}
        />
        <BarChart title="Cost" testId="chart-cost" points={series.points} bucket={series.bucket} value={(p) => p.cost_usd} fmt={fmtUsd} />
      </CardContent>
    </Card>
  );
}

/** Budget guards from settings: session spend cap (with top spenders) and per-turn limits. */
export function BudgetCard({ series, sessions }: { series: UsageSeries; sessions: Record<string, SessionInfo> | undefined }) {
  const b = series.budget;
  const cap = b.max_cost_usd;
  return (
    <Card className="gap-2" data-testid="budget-card">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          Budget guards
          <Badge variant={cap ? "info" : "secondary"} data-testid="budget-cap">
            {cap ? `${fmtUsd(cap)} per session` : "no spend cap"}
          </Badge>
        </CardTitle>
        <CardDescription>
          A session that reaches <code>max_cost_usd</code> stops with <code>BUDGET</code>. Per turn:{" "}
          {b.token_budget ? `${b.token_budget.toLocaleString()} completion tokens` : "no token budget"} ·{" "}
          {b.run_budget_seconds ? `${b.run_budget_seconds}s wall clock` : "no time budget"}.
          {!cap && (
            <>
              {" "}
              Set one with <code>auoreo config set max_cost_usd 5</code>.
            </>
          )}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-1.5">
        {(series.sessions ?? []).length === 0 && <p className="text-xs text-muted-foreground">No session spend in this range.</p>}
        {(series.sessions ?? []).map((s) => {
          const pct = s.ratio != null ? Math.min(100, s.ratio * 100) : null;
          return (
            <div key={s.session_id} className="space-y-0.5 text-xs" data-testid="budget-row">
              <div className="flex items-center gap-2">
                <Link to={`/session/${s.session_id}`} className="min-w-0 flex-1 truncate hover:underline">
                  {sessions?.[s.session_id]?.title || <span className="font-mono">{s.session_id}</span>}
                </Link>
                {s.blocked && (
                  <Badge variant="destructive">
                    <ShieldAlertIcon aria-hidden className="size-3" /> capped
                  </Badge>
                )}
                <span className="tabular-nums">
                  {fmtUsd(s.cost_usd)}
                  {cap ? ` / ${fmtUsd(cap)}` : ""}
                </span>
              </div>
              {pct !== null && (
                <div className="h-1.5 rounded bg-muted" role="meter" aria-valuenow={Math.round(pct)} aria-valuemin={0} aria-valuemax={100} aria-label="Share of the session spend cap">
                  <div className={cn("h-full rounded", pct >= 100 ? "bg-destructive" : pct >= 80 ? "bg-warning" : "bg-brand")} style={{ width: `${pct}%` }} />
                </div>
              )}
            </div>
          );
        })}
      </CardContent>
    </Card>
  );
}
