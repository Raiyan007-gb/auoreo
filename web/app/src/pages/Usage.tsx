import type { SessionInfo, UsageTotals } from "@sdk/client";
import { RefreshCwIcon } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";
import { EmptyState, ErrorNote, PageHeader } from "@/components/AppShell";
import { ContextCell } from "@/components/ContextGauge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { NativeSelect } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useAsync } from "@/hooks/useAsync";
import { api } from "@/lib/api";
import { fmtTokens, fmtUsd } from "@/lib/utils";

function Stat({ label, value, hint, testId }: { label: string; value: string; hint?: string; testId?: string }) {
  return (
    <Card className="gap-1">
      <CardHeader>
        <CardDescription>{label}</CardDescription>
        <CardTitle className="text-2xl tabular-nums" data-testid={testId}>
          {value}
        </CardTitle>
      </CardHeader>
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </Card>
  );
}

const tokens = (r: UsageTotals) => r.prompt_tokens + r.completion_tokens;

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
          {p && !p.enabled && <>Turn it on with <code>bandit.enabled: true</code> in settings.json.</>}
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
export function UsagePage() {
  const [days, setDays] = useState(7);
  const summary = useAsync(() => api.usageSummary(days), [days]);
  const sessions = useAsync(
    () =>
      api
        .listSessions(1000, true)
        .then((l) => Object.fromEntries(l.map((x) => [x.id, x])) as Record<string, SessionInfo>),
    [],
  );
  const s = summary.data;

  return (
    <div className="flex flex-col">
      <PageHeader
        title="Usage"
        description="Tokens and cost from the usage ledger."
        actions={
          <>
            <NativeSelect aria-label="Time range" value={days} onChange={(e) => setDays(Number(e.target.value))} className="w-36">
              <option value={1}>Last 24 hours</option>
              <option value={7}>Last 7 days</option>
              <option value={30}>Last 30 days</option>
              <option value={365}>Last year</option>
            </NativeSelect>
            <Button variant="outline" size="icon" aria-label="Refresh usage" onClick={summary.reload}>
              <RefreshCwIcon />
            </Button>
          </>
        }
      />
      <div className="space-y-4 p-4">
        <ErrorNote error={summary.error} />
        {s && (
          <>
            <div className="grid gap-3 sm:grid-cols-3">
              <Stat
                label="Total tokens"
                testId="usage-total-tokens"
                value={fmtTokens(tokens(s.total))}
                hint={`${s.total.prompt_tokens.toLocaleString()} prompt + ${s.total.completion_tokens.toLocaleString()} completion`}
              />
              <Stat label="Cost" testId="usage-total-cost" value={fmtUsd(s.total.cost_usd)} />
              <Stat label="Calls" testId="usage-total-calls" value={s.total.calls.toLocaleString()} />
            </div>
            <section className="space-y-2">
              <h2 className="text-sm font-medium">By model</h2>
              {s.by_model.length === 0 ? (
                <EmptyState title="No usage in this range">Run a turn and it shows up here.</EmptyState>
              ) : (
                <TotalsTable label="Model" rows={s.by_model} renderKey={(r) => <span className="font-mono text-xs">{r.key}</span>} />
              )}
            </section>
            {s.by_session.length > 0 && (
              <section className="space-y-2">
                <h2 className="text-sm font-medium">By session</h2>
                <TotalsTable
                  label="Session"
                  rows={s.by_session}
                  renderKey={(r) => (
                    <Link to={`/session/${r.key}`} className="hover:underline">
                      {sessions.data?.[r.key]?.title || <span className="font-mono text-xs">{r.key}</span>}
                    </Link>
                  )}
                  extra={{ head: "Context", cell: (r) => <ContextCell sessionId={r.key} /> }}
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
