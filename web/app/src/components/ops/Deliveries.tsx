import type { DeliveryInfo, DeliveryReplay } from "@sdk/client";
import { RotateCcwIcon } from "lucide-react";
import { useState } from "react";
import { ErrorNote } from "@/components/AppShell";
import { AsyncBody, Loading, RefreshButton, SessionLink, StatusBadge } from "@/components/ops/AsyncBody";
import { useAction } from "@/components/ops/opsUtils";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogTitle } from "@/components/ui/dialog";
import { Input, Label, NativeSelect } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useAsync } from "@/hooks/useAsync";
import { api } from "@/lib/api";
import { relTime } from "@/lib/utils";

const STATUSES = ["pending", "delivering", "delivered", "failed", "unknown"] as const;

/** Replay: always a dry run first (shows the stored prompt), then an explicit "Queue replay". */
function ReplayDialog({
  delivery,
  onClose,
  onQueued,
}: {
  delivery: DeliveryInfo | null;
  onClose: () => void;
  onQueued: () => void;
}) {
  const id = delivery?.id ?? null;
  const preview = useAsync<DeliveryReplay | undefined>(
    () => (id === null ? Promise.resolve(undefined) : api.replayDelivery(id, true)),
    [id],
  );
  const [queued, setQueued] = useState<DeliveryReplay | null>(null);
  const action = useAction();
  const close = () => {
    setQueued(null);
    action.clearError();
    onClose();
  };
  const queue = async () => {
    if (id === null) return;
    const r = await action.run("replay", () => api.replayDelivery(id, false));
    if (r) {
      setQueued(r);
      onQueued();
    }
  };
  return (
    <Dialog open={delivery !== null} onOpenChange={(o) => !o && close()}>
      <DialogContent className="sm:max-w-2xl" data-testid="replay-dialog">
        <DialogTitle>Replay delivery #{id}</DialogTitle>
        <DialogDescription>
          Dry run: this is the prompt that would be re-run for {delivery?.kind} <code>{delivery?.source}</code> →{" "}
          <code>{delivery?.target}</code>. Queueing creates a new pending delivery; the original is untouched.
        </DialogDescription>
        {preview.error ? (
          <ErrorNote error={preview.error} />
        ) : !preview.data || preview.data.dry_run !== true ? (
          <Loading label="Running dry run…" />
        ) : (
          <pre
            data-testid="replay-prompt"
            className="max-h-72 overflow-auto rounded-md border bg-muted/40 p-3 font-mono text-xs whitespace-pre-wrap"
          >
            {preview.data.prompt || "(empty prompt)"}
          </pre>
        )}
        <ErrorNote error={action.error} />
        {queued?.delivery && (
          <p role="status" className="text-sm text-success" data-testid="replay-queued">
            Queued as delivery #{queued.delivery.id} ({queued.delivery.status}).
          </p>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={close}>
            Close
          </Button>
          <Button onClick={() => void queue()} disabled={!preview.data || action.busy !== null || queued !== null}>
            <RotateCcwIcon aria-hidden /> Queue replay
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function DeliveriesTable({ rows, onReplay }: { rows: DeliveryInfo[]; onReplay: (d: DeliveryInfo) => void }) {
  return (
    <Table aria-label="Deliveries">
      <TableHeader>
        <TableRow>
          <TableHead>#</TableHead>
          <TableHead>Source</TableHead>
          <TableHead>Target</TableHead>
          <TableHead>Status</TableHead>
          <TableHead>Attempts</TableHead>
          <TableHead>Session</TableHead>
          <TableHead>Updated</TableHead>
          <TableHead>
            <span className="sr-only">Actions</span>
          </TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((d) => (
          <TableRow key={d.id} data-testid="delivery-row" data-id={d.id} data-status={d.status}>
            <TableCell className="font-mono text-xs">{d.id}</TableCell>
            <TableCell>
              <p className="text-xs font-medium">{d.source}</p>
              <p className="text-[11px] text-muted-foreground">{d.kind}</p>
            </TableCell>
            <TableCell className="font-mono text-xs">{d.target}</TableCell>
            <TableCell>
              <StatusBadge status={d.status} />
              {d.error && <p className="mt-1 max-w-xs text-xs text-destructive">{d.error}</p>}
            </TableCell>
            <TableCell className="text-xs">{d.attempts}</TableCell>
            <TableCell>
              <SessionLink id={d.session_id} />
            </TableCell>
            <TableCell className="text-xs whitespace-nowrap text-muted-foreground" title={d.updated_at}>
              {relTime(d.updated_at)}
            </TableCell>
            <TableCell>
              <Button size="sm" variant="outline" onClick={() => onReplay(d)} aria-label={`Replay delivery ${d.id}`}>
                <RotateCcwIcon aria-hidden /> Replay
              </Button>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

/**
 * Deliveries outbox with status/kind filters and replay. `source` narrows to one job or
 * webhook route (client-side; the API filters by status and kind only). `tick` forces a refetch.
 */
export function DeliveriesPanel({ source, kind: fixedKind, tick = 0 }: { source?: string; kind?: string; tick?: number }) {
  const [status, setStatus] = useState("");
  const [kind, setKind] = useState(fixedKind ?? "");
  const list = useAsync(
    () => api.deliveries({ status: status || null, kind: kind || null, limit: 200 }),
    [status, kind, tick],
  );
  const [replay, setReplay] = useState<DeliveryInfo | null>(null);
  const rows = source ? list.data?.filter((d) => d.source === source) : list.data;
  const view = { ...list, data: rows };
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-end gap-2">
        <div className="space-y-1">
          <Label htmlFor={`dl-status-${source ?? "all"}`}>Status</Label>
          <NativeSelect
            id={`dl-status-${source ?? "all"}`}
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            className="w-36"
          >
            <option value="">any</option>
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </NativeSelect>
        </div>
        {fixedKind === undefined && (
          <div className="space-y-1">
            <Label htmlFor="dl-kind">Kind</Label>
            <Input id="dl-kind" placeholder="job, webhook…" value={kind} onChange={(e) => setKind(e.target.value)} className="w-36" />
          </div>
        )}
        <div className="ml-auto">
          <RefreshButton onClick={list.reload} busy={list.loading} label="Refresh deliveries" />
        </div>
      </div>
      <AsyncBody
        state={view}
        empty="No deliveries yet"
        emptyHint="Rows appear here when a job or webhook run is delivered (or fails)."
      >
        {(r) => <DeliveriesTable rows={r} onReplay={setReplay} />}
      </AsyncBody>
      <ReplayDialog key={replay?.id ?? "none"} delivery={replay} onClose={() => setReplay(null)} onQueued={list.reload} />
    </div>
  );
}
