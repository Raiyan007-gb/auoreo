import type { DeliveryInfo, WebhookRouteInfo } from "@sdk/client";
import { InboxIcon, RotateCcwIcon } from "lucide-react";
import { useMemo, useState } from "react";
import { AsyncBody, Loading, RefreshButton, StatusBadge } from "@/components/ops/AsyncBody";
import { DeliveriesPanel, ReplayDialog } from "@/components/ops/Deliveries";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useAsync } from "@/hooks/useAsync";
import { api } from "@/lib/api";
import { relTime } from "@/lib/utils";

/** Newest delivery per webhook route (`source` = route name). */
function lastBySource(rows: DeliveryInfo[] | undefined): Map<string, DeliveryInfo> {
  const out = new Map<string, DeliveryInfo>();
  for (const d of rows ?? []) {
    const prev = out.get(d.source);
    if (!prev || d.id > prev.id) out.set(d.source, d);
  }
  return out;
}

function WebhookRow({
  w,
  last,
  onDeliveries,
  onReplay,
}: {
  w: WebhookRouteInfo;
  last: DeliveryInfo | undefined;
  onDeliveries: () => void;
  onReplay: (d: DeliveryInfo) => void;
}) {
  return (
    <TableRow data-testid="webhook-row" data-name={w.name}>
      <TableCell className="max-w-xs">
        <p className="font-mono text-xs font-medium">POST {w.path}</p>
        <p className="line-clamp-2 text-xs text-muted-foreground">{w.description}</p>
      </TableCell>
      <TableCell>
        <Badge variant="outline">{w.provider}</Badge>
      </TableCell>
      <TableCell>
        <div className="flex flex-wrap gap-1">
          {w.events.length ? w.events.map((e) => <Badge key={e}>{e}</Badge>) : <span className="text-xs text-muted-foreground">any</span>}
        </div>
        {w.filters.length > 0 && <p className="text-[11px] text-muted-foreground">{w.filters.length} filters</p>}
      </TableCell>
      <TableCell className="text-xs">
        {w.workflow ? (
          <>
            workflow <code>{w.workflow}</code>
          </>
        ) : (
          <code>{w.agent}</code>
        )}
      </TableCell>
      <TableCell>
        <Badge variant={w.enabled ? "success" : "outline"}>{w.enabled ? "enabled" : "disabled"}</Badge>
      </TableCell>
      <TableCell>
        <Badge
          variant={w.secret_configured ? "success" : "warning"}
          title={w.secret_configured ? "The referenced secret resolves (value never shown)" : `Set it with: auoreo secrets set webhook/${w.name}`}
        >
          {w.secret_configured ? "configured" : "missing"}
        </Badge>
      </TableCell>
      <TableCell data-testid="webhook-last">
        {last ? (
          <div className="flex flex-col gap-0.5">
            <StatusBadge status={last.status} />
            <span className="text-[11px] text-muted-foreground" title={last.updated_at}>
              #{last.id} · {relTime(last.updated_at)}
            </span>
          </div>
        ) : (
          <span className="text-xs text-muted-foreground">never</span>
        )}
      </TableCell>
      <TableCell>
        <div className="flex gap-1">
          <Button size="sm" variant="outline" onClick={onDeliveries} aria-label={`Deliveries of ${w.name}`}>
            <InboxIcon aria-hidden /> Deliveries
          </Button>
          {last && (
            <Button size="sm" variant="ghost" onClick={() => onReplay(last)} aria-label={`Replay last delivery of ${w.name}`}>
              <RotateCcwIcon aria-hidden /> Replay
            </Button>
          )}
        </div>
      </TableCell>
    </TableRow>
  );
}

/**
 * Webhook route table (route, provider, events, agent, enabled, secret state, last delivery)
 * with a per-route deliveries drawer and replay (dry run first, then an explicit confirm).
 */
export function WebhookRoutes({ tick }: { tick: number }) {
  const hooks = useAsync(() => api.webhooks(), []);
  const deliveries = useAsync(() => api.deliveries({ kind: "webhook", limit: 500 }), [tick]);
  const last = useMemo(() => lastBySource(deliveries.data), [deliveries.data]);
  const [drawer, setDrawer] = useState<string | null>(null);
  const [replay, setReplay] = useState<DeliveryInfo | null>(null);
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <p className="flex-1 text-xs text-muted-foreground">
          HMAC-verified ingress routes from <code>webhooks/&lt;route&gt;.yaml</code>. Secrets are never shown.
        </p>
        <RefreshButton
          onClick={() => {
            hooks.reload();
            deliveries.reload();
          }}
          busy={hooks.loading}
          label="Refresh webhooks"
        />
      </div>
      <AsyncBody state={hooks} empty="No webhook routes" emptyHint="Add webhooks/<route>.yaml to a definition root.">
        {(rows) => (
          <Table aria-label="Webhook routes">
            <TableHeader>
              <TableRow>
                <TableHead>Route</TableHead>
                <TableHead>Provider</TableHead>
                <TableHead>Events</TableHead>
                <TableHead>Agent</TableHead>
                <TableHead>Enabled</TableHead>
                <TableHead>Secret</TableHead>
                <TableHead>Last delivery</TableHead>
                <TableHead>
                  <span className="sr-only">Actions</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((w) => (
                <WebhookRow key={w.name} w={w} last={last.get(w.name)} onDeliveries={() => setDrawer(w.name)} onReplay={setReplay} />
              ))}
            </TableBody>
          </Table>
        )}
      </AsyncBody>
      <Sheet open={drawer !== null} onOpenChange={(o) => !o && setDrawer(null)}>
        <SheetContent data-testid="deliveries-drawer">
          <SheetHeader>
            <SheetTitle>Deliveries of {drawer}</SheetTitle>
            <SheetDescription>Replay runs a dry run first and only queues a new delivery when you confirm.</SheetDescription>
          </SheetHeader>
          {drawer ? <DeliveriesPanel source={drawer} kind="webhook" tick={tick} /> : <Loading />}
        </SheetContent>
      </Sheet>
      <ReplayDialog key={replay?.id ?? "none"} delivery={replay} onClose={() => setReplay(null)} onQueued={deliveries.reload} />
    </div>
  );
}
