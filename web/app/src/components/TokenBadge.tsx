import type { UsageRecord } from "@sdk/client";
import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Tip } from "@/components/ui/tooltip";
import { api } from "@/lib/api";
import type { Usage } from "@/lib/thread";
import { fmtTokens, fmtUsd } from "@/lib/utils";

/**
 * Token/cost badge with the per-call usage ledger in its tooltip (Session header; Jobs and
 * Teams run transcripts reuse it — I5). The ledger is refetched whenever a run ends.
 */
export function TokenBadge({ sessionId, usage, running }: { sessionId: string; usage: Usage; running: boolean }) {
  const [ledger, setLedger] = useState<UsageRecord[] | null>(null);
  useEffect(() => {
    if (running) return;
    let cancelled = false;
    api
      .usage(sessionId)
      .then((rows) => !cancelled && setLedger(rows))
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [sessionId, running, usage.prompt]);

  const totals = ledger?.reduce(
    (acc, r) => ({
      calls: acc.calls + 1,
      prompt: acc.prompt + r.prompt_tokens,
      completion: acc.completion + r.completion_tokens,
      cached: acc.cached + r.cached_tokens,
      cost: acc.cost + (r.cost_usd ?? 0),
      estimated: acc.estimated || r.estimated,
    }),
    { calls: 0, prompt: 0, completion: 0, cached: 0, cost: 0, estimated: false },
  );
  const models = [...new Set(ledger?.map((r) => r.model) ?? [])];
  // Live counters come from `usage` events; a transcript opened after the run has only the ledger.
  const tokens = usage.prompt + usage.completion || (totals ? totals.prompt + totals.completion : 0);
  const cost = usage.cost || (totals?.cost ?? 0);
  return (
    <Tip
      label={
        totals && totals.calls > 0 ? (
          <span data-testid="usage-tooltip">
            Usage ledger: {totals.calls} call{totals.calls === 1 ? "" : "s"} · {totals.prompt.toLocaleString()} prompt +{" "}
            {totals.completion.toLocaleString()} completion
            {totals.cached > 0 && ` (${totals.cached.toLocaleString()} cached)`} · {fmtUsd(totals.cost)}
            {totals.estimated && " (estimated)"}
            {models.length > 0 && <> · {models.join(", ")}</>}
          </span>
        ) : (
          `${usage.prompt.toLocaleString()} prompt + ${usage.completion.toLocaleString()} completion · ${fmtUsd(usage.cost)}`
        )
      }
    >
      <Badge variant="secondary" data-testid="token-badge" tabIndex={0}>
        {fmtTokens(tokens)} tok · {fmtUsd(cost)}
      </Badge>
    </Tip>
  );
}
