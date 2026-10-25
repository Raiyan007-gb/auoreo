import { useState } from "react";
import { Link } from "react-router";
import { EmptyState, ErrorNote, PageHeader } from "@/components/AppShell";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { NativeSelect } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useAsync, useSupports } from "@/hooks/useAsync";
import { api } from "@/lib/api";
import { fmtTokens, fmtUsd } from "@/lib/utils";

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <Card className="gap-1">
      <CardHeader>
        <CardDescription>{label}</CardDescription>
        <CardTitle className="text-2xl tabular-nums">{value}</CardTitle>
      </CardHeader>
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </Card>
  );
}

export function UsagePage() {
  const supported = useSupports("usageSummary");
  const [days, setDays] = useState(7);
  const summary = useAsync(
    () => (supported ? api.usageSummary(days) : Promise.resolve(undefined)),
    [days, supported],
  );
  const s = summary.data;

  return (
    <div className="flex flex-col">
      <PageHeader
        title="Usage"
        description="Tokens and cost from the usage ledger."
        actions={
          <NativeSelect aria-label="Time range" value={days} onChange={(e) => setDays(Number(e.target.value))} className="w-36">
            <option value={1}>Last 24 hours</option>
            <option value={7}>Last 7 days</option>
            <option value={30}>Last 30 days</option>
            <option value={365}>Last year</option>
          </NativeSelect>
        }
      />
      <div className="space-y-4 p-4">
        {supported === false && (
          <EmptyState title="Usage ledger available after upgrade">
            This server does not expose <code className="font-mono">/api/usage/summary</code> yet. Per-session token
            counts are shown live on each session page.
          </EmptyState>
        )}
        <ErrorNote error={summary.error} />
        {s && (
          <>
            <div className="grid gap-3 sm:grid-cols-3">
              <Stat label="Tokens" value={fmtTokens(s.total_tokens)} />
              <Stat label="Cost" value={fmtUsd(s.total_cost_usd)} />
              <Stat label="Runs" value={String(s.runs)} />
            </div>
            {s.by_model.length > 0 && (
              <section className="space-y-2">
                <h2 className="text-sm font-medium">By model</h2>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Model</TableHead>
                      <TableHead className="text-right">Runs</TableHead>
                      <TableHead className="text-right">Tokens</TableHead>
                      <TableHead className="text-right">Cost</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {s.by_model.map((m) => (
                      <TableRow key={m.model}>
                        <TableCell className="font-mono text-xs">{m.model}</TableCell>
                        <TableCell className="text-right tabular-nums">{m.runs}</TableCell>
                        <TableCell className="text-right tabular-nums">{fmtTokens(m.tokens)}</TableCell>
                        <TableCell className="text-right tabular-nums">{fmtUsd(m.cost_usd)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </section>
            )}
            <section className="space-y-2">
              <h2 className="text-sm font-medium">By session</h2>
              {s.by_session.length === 0 ? (
                <EmptyState title="No usage in this range" />
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Session</TableHead>
                      <TableHead className="text-right">Runs</TableHead>
                      <TableHead className="text-right">Tokens</TableHead>
                      <TableHead className="text-right">Cost</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {s.by_session.map((r) => (
                      <TableRow key={r.session_id}>
                        <TableCell>
                          <Link to={`/session/${r.session_id}`} className="hover:underline">
                            {r.title || r.session_id}
                          </Link>
                        </TableCell>
                        <TableCell className="text-right tabular-nums">{r.runs}</TableCell>
                        <TableCell className="text-right tabular-nums">{fmtTokens(r.tokens)}</TableCell>
                        <TableCell className="text-right tabular-nums">{fmtUsd(r.cost_usd)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </section>
          </>
        )}
      </div>
    </div>
  );
}
