/** G6: PREFS.md proposals inbox — the agent proposes, you approve (applied with the approval event id) or deny. */
import type { PrefsProposal } from "@sdk/client";
import { CheckIcon, XIcon } from "lucide-react";
import { useState } from "react";
import { EmptyState, ErrorNote } from "@/components/AppShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useAsync } from "@/hooks/useAsync";
import { api } from "@/lib/api";
import { errorText } from "@/lib/utils";

function ProposalRow({ p, onDone }: { p: PrefsProposal; onDone: () => void }) {
  const [err, setErr] = useState<string | null>(null);
  const act = async (fn: () => Promise<unknown>) => {
    setErr(null);
    try {
      await fn();
      onDone();
    } catch (e) {
      setErr(errorText(e));
    }
  };
  return (
    <li className="rounded-lg border p-3" data-testid="prefs-proposal" data-proposal-id={p.id}>
      <div className="flex flex-wrap items-center gap-1.5">
        <Badge variant="secondary">{p.action}</Badge>
        {p.old_text && <span className="text-xs text-muted-foreground line-through">{p.old_text}</span>}
        <span className="min-w-0 flex-1">{p.content}</span>
        <Button variant="brand" size="sm" aria-label={`Approve preference ${p.id}`} onClick={() => void act(() => api.approvePref(p.id))}>
          <CheckIcon aria-hidden /> Approve
        </Button>
        <Button variant="outline" size="sm" aria-label={`Deny preference ${p.id}`} onClick={() => void act(() => api.denyPref(p.id))}>
          <XIcon aria-hidden /> Deny
        </Button>
      </div>
      {p.reason && <p className="mt-1 text-xs text-muted-foreground">why: {p.reason}</p>}
      {err && <ErrorNote error={err} />}
    </li>
  );
}

export function PrefsPanel() {
  const pending = useAsync(() => api.prefsProposals("pending"), []);
  const items = pending.data ?? [];
  return (
    <Card className="lg:col-span-2" aria-label="Preference proposals">
      <CardHeader>
        <CardTitle>Preference proposals</CardTitle>
        <CardDescription>
          The agent never writes PREFS.md itself: it proposes, you decide. Approved entries record the approval event id.
        </CardDescription>
      </CardHeader>
      {pending.error ? <ErrorNote error={errorText(pending.error)} /> : null}
      {items.length === 0 ? (
        <EmptyState title="No pending proposals">Proposals appear here when the agent learns a preference.</EmptyState>
      ) : (
        <ul className="space-y-2">
          {items.map((p) => (
            <ProposalRow key={p.id} p={p} onDone={pending.reload} />
          ))}
        </ul>
      )}
    </Card>
  );
}
