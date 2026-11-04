import type { SessionContext, SessionInfo, UsageTotals } from "@sdk/client";
import { RefreshCwIcon } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";
import { EmptyState, ErrorNote, PageHeader } from "@/components/AppShell";
import { ContextCell } from "@/components/ContextGauge";
import { BudgetCard, UsageCharts } from "@/components/UsageCharts";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useAsync } from "@/hooks/useAsync";
import { api } from "@/lib/api";
import { cn, fmtTokens, fmtUsd } from "@/lib/utils";

function Stat({ label, value, hint, testId }: { label: string; value: string; hint?: string; testId?: string }) {
  return (
    <Card className="min-w-0 flex-[1_1_180px] gap-0.5 px-4 py-3.5 sm:px-4 sm:py-3.5">
      <CardHeader className="gap-0.5">
        <CardDescription>{label}</CardDescription>
        <CardTitle className="text-2xl font-semibold tracking-tight tabular-nums" data-testid={testId}>
          {value}
        </CardTitle>
      </CardHeader>
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </Card>
  );
}

const tokens = (r: UsageTotals) => r.prompt_tokens + r.completion_tokens;
/** Share of the prompt served from the provider cache, as a whole percent. */
const pct = (ratio: number | undefined) => `${Math.round((ratio ?? 0) * 100)}%`;

function TotalsTable({
  label,
  rows,
  renderKey,
  extra,
}: {
  label: string;
  rows: readonly UsageTotals[];
  renderKey: (r: UsageTotals) => React.ReactNode;
  /** Optional trailing column (G5b: per-session context gauge). */
  extra?: { head: string; cell: (r: UsageTotals) => React.ReactNode };
}) {
  return (
    <Table aria-label={`Usage by ${label.toLowerCase()}`}>
      <TableHeader>
        <TableRow>
          <TableHead>{label}</TableHead>
          <TableHead className="text-right">Calls</TableHead>
          <TableHead className="text-right">Prompt</TableHead>
          <TableHead className="text-right">Completion</TableHead>
          <TableHead className="text-right">Cached</TableHead>
          <TableHead className="text-right">Cache hit</TableHead>
          <TableHead className="text-right">Total</TableHead>
          <TableHead className="text-right">Cost</TableHead>
          {extra && <TableHead>{extra.head}</TableHead>}
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((r) => (
          <TableRow key={r.key}>
            <TableCell>{renderKey(r)}</TableCell>
            <TableCell className="text-right tabular-nums">{r.calls}</TableCell>
            <TableCell className="text-right tabular-nums">{fmtTokens(r.prompt_tokens)}</TableCell>
            <TableCell className="text-right tabular-nums">{fmtTokens(r.completion_tokens)}</TableCell>
            <TableCell className="text-right tabular-nums">{fmtTokens(r.cached_tokens ?? 0)}</TableCell>
            <TableCell className="text-right tabular-nums">{pct(r.cache_hit_ratio)}</TableCell>
            <TableCell className="text-right tabular-nums">{fmtTokens(tokens(r))}</TableCell>
            <TableCell className="text-right tabular-nums">{fmtUsd(r.cost_usd)}</TableCell>
            {extra && <TableCell>{extra.cell(r)}</TableCell>}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

/** `GET /api/policy/bandit` — the model/effort bandit (off unless `bandit.enabled`). */
function PolicyCard() {
  const status = useAsync(() => api.banditStatus(), []);
  const [busy, setBusy] = useState(false);
  const p = status.data;
  const reset = async () => {
    setBusy(true);
    try {
      await api.resetBandit();
      status.reload();
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card className="gap-2" data-testid="policy-card" aria-label="Model policy">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          Model policy
          {p && (
            <Badge variant={p.enabled ? "success" : "secondary"} data-testid="policy-state">
              {p.enabled ? "learning" : "off"}
            </Badge>
          )}
        </CardTitle>
        <CardDescription>
          Epsilon-greedy choice of model and effort per agent, applied at turn boundaries.{" "}
          {p && !p.enabled && <>Turn it on with <code>auoreo config set bandit.enabled true</code> (or Settings → Environment).</>}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        <ErrorNote error={status.error} />
        {p && (
          <>
            <p className="text-xs text-muted-foreground">
              ε {p.epsilon} − {p.epsilon_decay} per 100 turns (floor {p.min_epsilon}) · arms:{" "}
              <span className="font-mono">{p.arms.map((a) => a.key).join(", ")}</span>
            </p>
            {p.stats.length === 0 ? (
              <p className="text-xs text-muted-foreground">No outcomes recorded yet.</p>
            ) : (
              <Table aria-label="Model policy arms">
                <TableHeader>
                  <TableRow>
                    <TableHead>Context</TableHead>
                    <TableHead>Arm</TableHead>
                    <TableHead className="text-right">Turns</TableHead>
                    <TableHead className="text-right">Mean reward</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {p.stats.map((r) => (
                    <TableRow key={`${r.context_key}/${r.key}`}>
                      <TableCell className="font-mono text-xs">{r.context_key}</TableCell>
                      <TableCell className="font-mono text-xs">{r.key}</TableCell>
                      <TableCell className="text-right tabular-nums">{r.pulls}</TableCell>
                      <TableCell className="text-right tabular-nums">{r.mean_reward.toFixed(3)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
            {p.stats.length > 0 && (
              <Button variant="outline" size="sm" disabled={busy} onClick={() => void reset()}>
                Reset learned stats
              </Button>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}

/** `GET /api/usage/summary?days=` — totals, per model and per session. */
const RANGES: readonly (readonly [number, string])[] = [
  [1, "Today"],
  [7, "7 days"],
  [30, "30 days"],
  [365, "Year"],
];

export function UsagePage() {
  const [days, setDays] = useState(7);
  const summary = useAsync(() => api.usageSummary(days), [days]);
  const series = useAsync(() => api.usageTimeseries(days), [days]); // --- G7: charts + budget guards
  const sessions = useAsync(
    () =>
      api
        .listSessions(1000, true)
        .then((l) => Object.fromEntries(l.map((x) => [x.id, x])) as Record<string, SessionInfo>),
    [],
  );
  const s = summary.data;
  // Every listed session's context gauge in one request (the API caps a batch at 100).
  const ctxIds = (s?.by_session ?? []).slice(0, 100).map((r) => r.key).join(" ");
  const contexts = useAsync(
    () =>
      ctxIds
        ? api.sessionsContext(ctxIds.split(" ")).then((l) => new Map(l.map((c) => [c.session_id, c])))
        : Promise.resolve(new Map<string, SessionContext>()),
    [ctxIds],
  );

  return (
    <div className="flex flex-col">
      <PageHeader
        title="Usage"
        description="What your agents spent, by day, model and session. Costs use the provider’s list prices."
        actions={
          <>
            <div role="group" aria-label="Time range" className="flex gap-1 rounded-[9px] bg-accent p-[3px]">
              {RANGES.map(([d, label]) => (
                <button
                  key={d}
                  type="button"
                  aria-pressed={days === d}
                  onClick={() => setDays(d)}
                  className={cn(
                    "h-7.5 rounded-[7px] px-3 text-ui text-secondary-foreground",
                    days === d && "bg-card font-medium text-foreground shadow-xs",
                  )}
                >
                  {label}
                </button>
              ))}
            </div>
            <Button
              variant="outline"
              size="icon"
              aria-label="Refresh usage"
              onClick={() => {
                summary.reload();
                series.reload();
              }}
            >
              <RefreshCwIcon />
            </Button>
          </>
        }
      />
      <div className="space-y-4 page-body">
        <ErrorNote error={summary.error} />
        {s && (
          <>
            <div className="flex flex-wrap gap-3.5">
              <Stat
                label="Total tokens"
                testId="usage-total-tokens"
                value={fmtTokens(tokens(s.total))}
                hint={`${s.total.prompt_tokens.toLocaleString()} prompt + ${s.total.completion_tokens.toLocaleString()} completion`}
              />
              <Stat
                label="Cache hit"
                testId="usage-cache-hit"
                value={pct(s.total.cache_hit_ratio)}
                hint={`${(s.total.cached_tokens ?? 0).toLocaleString()} cached, ${(s.total.cache_write_tokens ?? 0).toLocaleString()} written`}
              />
              <Stat label="Cost" testId="usage-total-cost" value={fmtUsd(s.total.cost_usd)} />
              <Stat label="Model calls" testId="usage-total-calls" value={s.total.calls.toLocaleString()} hint={`across ${s.by_session.length} session${s.by_session.length === 1 ? "" : "s"}`} />
              <Stat label="Average call" value={s.total.calls ? fmtUsd(s.total.cost_usd / s.total.calls) : "—"} hint="cost per model call" />
            </div>
            {series.data && (
              <div className="grid gap-3 xl:grid-cols-[2fr_1fr]">
                <UsageCharts series={series.data} />
                <BudgetCard series={series.data} sessions={sessions.data} />
              </div>
            )}
            <section className="space-y-2">
              <h2 className="text-title font-semibold">By model</h2>
              {s.by_model.length === 0 ? (
                <EmptyState title="No usage in this range">Run a turn and it shows up here.</EmptyState>
              ) : (
                <TotalsTable label="Model" rows={s.by_model} renderKey={(r) => <span className="font-mono text-xs">{r.key}</span>} />
              )}
            </section>
            {s.by_session.length > 0 && (
              <section className="space-y-2">
                <h2 className="text-title font-semibold">By session</h2>
                <TotalsTable
                  label="Session"
                  rows={s.by_session}
                  renderKey={(r) => (
                    <Link to={`/session/${r.key}`} className="hover:underline">
                      {sessions.data?.[r.key]?.title || <span className="font-mono text-xs">{r.key}</span>}
                    </Link>
                  )}
                  extra={{ head: "Context", cell: (r) => <ContextCell sessionId={r.key} ctx={contexts.data?.get(r.key)} /> }}
                />
              </section>
            )}
          </>
        )}
        <PolicyCard />
      </div>
    </div>
  );
}
