import type { AuditEntry, AuditQuery } from "@sdk/client";
import { RefreshCwIcon, ShieldAlertIcon, ShieldCheckIcon } from "lucide-react";
import { useState } from "react";
import { Link, useSearchParams } from "react-router";
import { EmptyState, ErrorNote, PageHeader } from "@/components/AppShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Input, Label } from "@/components/ui/input";
import { DensityToggle, Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useAsync } from "@/hooks/useAsync";
import { api } from "@/lib/api";
import { relTime } from "@/lib/utils";

const PAGE = 200;

function ChainBadge() {
  const v = useAsync(() => api.auditVerify(), []);
  if (v.error) return <ErrorNote error={v.error} />;
  if (!v.data) return <Badge variant="outline">verifying…</Badge>;
  return v.data.ok ? (
    <Badge variant="success" data-testid="chain-status" data-ok="true" title="SHA-256 hash chain recomputed from the first row">
      <ShieldCheckIcon aria-hidden /> chain ok · {v.data.rows} rows
    </Badge>
  ) : (
    <Badge variant="destructive" data-testid="chain-status" data-ok="false">
      <ShieldAlertIcon aria-hidden /> chain broken at seq {v.data.first_break_seq}
    </Badge>
  );
}

function SessionLink({ id }: { id: string | null | undefined }) {
  if (!id) return <span className="text-muted-foreground">—</span>;
  return (
    <Link to={`/session/${encodeURIComponent(id)}`} className="font-mono text-xs underline-offset-2 hover:underline">
      {id.length > 14 ? `${id.slice(0, 14)}…` : id}
    </Link>
  );
}

function decisionVariant(d: string) {
  if (/deny|reject|block/i.test(d)) return "destructive" as const;
  if (/allow|approve|once|always/i.test(d)) return "success" as const;
  if (/escalate|warn|ask/i.test(d)) return "warning" as const;
  return "secondary" as const;
}

function RowDetail({ row, onClose }: { row: AuditEntry | null; onClose: () => void }) {
  return (
    <Dialog open={row !== null} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-2xl" data-testid="audit-detail">
        {row && (
          <>
            <DialogTitle>
              Audit #{row.seq} · {row.action}
            </DialogTitle>
            <DialogDescription>
              {new Date(row.ts).toLocaleString()} by {row.actor}
              {row.decision && ` · ${row.decision}`}
              {row.reason && ` (${row.reason})`}
            </DialogDescription>
            {row.session_id && (
              <p className="text-sm">
                Session: <SessionLink id={row.session_id} />
              </p>
            )}
            <pre className="max-h-80 overflow-auto rounded-md bg-muted p-2 font-mono text-xs">
              {JSON.stringify(row.detail, null, 2)}
            </pre>
            <dl className="grid grid-cols-[6rem_1fr] gap-y-1 font-mono text-2xs break-all text-muted-foreground">
              <dt>prev_hash</dt>
              <dd>{row.prev_hash}</dd>
              <dt>row_hash</dt>
              <dd>{row.row_hash}</dd>
            </dl>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}

export function AuditPage() {
  // `?session_id=&action=` deep links (e.g. the Approvals page's per-decision audit link).
  const [params] = useSearchParams();
  const [sessionId, setSessionId] = useState(params.get("session_id") ?? "");
  const [action, setAction] = useState(params.get("action") ?? "");
  const [query, setQuery] = useState<AuditQuery>({
    newest_first: true,
    limit: PAGE,
    session_id: params.get("session_id") || undefined,
    action: params.get("action") || undefined,
  });
  const [selected, setSelected] = useState<AuditEntry | null>(null);
  const page = useAsync(() => api.audit(query), [query]);
  const [verifyRun, setVerifyRun] = useState(0); // the chain is re-verified on Refresh only

  const apply = () =>
    setQuery({
      newest_first: true,
      limit: PAGE,
      session_id: sessionId.trim() || undefined,
      action: action.trim() || undefined,
    });

  return (
    <div className="flex flex-col">
      <PageHeader
        title="Audit"
        description="Append-only, hash-chained log of tool calls, permission decisions and memory changes."
        actions={
          <>
            <DensityToggle />
            <ChainBadge key={verifyRun} />
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                page.reload();
                setVerifyRun((n) => n + 1);
              }}
              aria-label="Refresh audit log"
            >
              <RefreshCwIcon aria-hidden /> Refresh
            </Button>
          </>
        }
      />
      <div className="space-y-3 page-body">
        <form
          className="flex flex-wrap items-end gap-2"
          aria-label="Filter audit log"
          onSubmit={(e) => {
            e.preventDefault();
            apply();
          }}
        >
          <div className="grid gap-1">
            <Label htmlFor="audit-session">Session id</Label>
            <Input id="audit-session" className="w-56" value={sessionId} onChange={(e) => setSessionId(e.target.value)} />
          </div>
          <div className="grid gap-1">
            <Label htmlFor="audit-action">Action</Label>
            <Input id="audit-action" className="w-48" placeholder="e.g. tool.call" value={action} onChange={(e) => setAction(e.target.value)} />
          </div>
          <Button type="submit" size="sm">
            Filter
          </Button>
        </form>
        <ErrorNote error={page.error} />
        {page.data && page.data.rows.length === 0 && <EmptyState title="No audit rows match" />}
        {page.data && page.data.rows.length > 0 && (
          <Table aria-label="Audit log">
            <TableHeader>
              <TableRow>
                <TableHead className="w-16">Seq</TableHead>
                <TableHead>When</TableHead>
                <TableHead>Actor</TableHead>
                <TableHead>Action</TableHead>
                <TableHead>Decision</TableHead>
                <TableHead>Session</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {page.data.rows.map((r) => (
                <TableRow
                  key={r.seq}
                  data-testid="audit-row"
                  className="cursor-pointer"
                  tabIndex={0}
                  onClick={() => setSelected(r)}
                  onKeyDown={(e) => e.key === "Enter" && setSelected(r)}
                >
                  <TableCell className="font-mono tabular-nums">{r.seq}</TableCell>
                  <TableCell title={new Date(r.ts).toLocaleString()}>{relTime(r.ts)}</TableCell>
                  <TableCell>{r.actor}</TableCell>
                  <TableCell className="font-mono text-xs">{r.action}</TableCell>
                  <TableCell>{r.decision ? <Badge variant={decisionVariant(r.decision)}>{r.decision.toLowerCase()}</Badge> : null}</TableCell>
                  <TableCell onClick={(e) => e.stopPropagation()}>
                    <SessionLink id={r.session_id} />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
        <p className="text-xs text-muted-foreground">
          Showing the newest {PAGE} matching rows. The CLI check <code className="font-mono">auoreo doctor</code> verifies the same chain.
        </p>
      </div>
      <RowDetail row={selected} onClose={() => setSelected(null)} />
    </div>
  );
}
