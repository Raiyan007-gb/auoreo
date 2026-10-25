import type { CronJobInfo, WebhookRouteInfo } from "@sdk/client";
import { HistoryIcon, InboxIcon, PauseIcon, PlayIcon } from "lucide-react";
import { useState } from "react";
import { useSearchParams } from "react-router";
import { ErrorNote, PageHeader } from "@/components/AppShell";
import { AsyncBody, Loading, RefreshButton, SessionLink, StatusBadge } from "@/components/ops/AsyncBody";
import { DeliveriesPanel } from "@/components/ops/Deliveries";
import { fmtWhen, useAction, useServerEvents } from "@/components/ops/opsUtils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useAsync } from "@/hooks/useAsync";
import { api } from "@/lib/api";

const TABS = ["cron", "webhooks", "deliveries"] as const;
type Tab = (typeof TABS)[number];

function duration(a: string | null, b: string | null): string {
  if (!a || !b) return "—";
  const ms = Date.parse(b) - Date.parse(a);
  return Number.isNaN(ms) ? "—" : ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
}

function RunsDrawer({ job, tick, onClose }: { job: string | null; tick: number; onClose: () => void }) {
  const runs = useAsync(() => (job ? api.cronRuns(job, 50) : Promise.resolve([])), [job, tick]);
  return (
    <Sheet open={job !== null} onOpenChange={(o) => !o && onClose()}>
      <SheetContent data-testid="runs-drawer">
        <SheetHeader>
          <SheetTitle>Runs of {job}</SheetTitle>
          <SheetDescription>Most recent first. Each run is an agent session; its result goes to the job's delivery targets.</SheetDescription>
        </SheetHeader>
        <div className="flex justify-end">
          <RefreshButton onClick={runs.reload} busy={runs.loading} label="Refresh runs" />
        </div>
        <AsyncBody state={runs} empty="No runs yet" emptyHint="Use “Run now” to fire the job outside its schedule.">
          {(rows) => (
            <Table aria-label={`Runs of ${job ?? ""}`}>
              <TableHeader>
                <TableRow>
                  <TableHead>#</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Scheduled for</TableHead>
                  <TableHead>Took</TableHead>
                  <TableHead>Session</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((r) => (
                  <TableRow key={r.id} data-testid="run-row" data-status={r.status}>
                    <TableCell className="font-mono text-xs">{r.id}</TableCell>
                    <TableCell>
                      <StatusBadge status={r.status} />
                      {r.catch_up && <Badge variant="outline" className="ml-1">catch-up</Badge>}
                      {r.error && <p className="mt-1 max-w-xs text-xs text-destructive">{r.error}</p>}
                    </TableCell>
                    <TableCell className="text-xs whitespace-nowrap">{fmtWhen(r.scheduled_for)}</TableCell>
                    <TableCell className="text-xs">{duration(r.started_at, r.finished_at)}</TableCell>
                    <TableCell>
                      <SessionLink id={r.session_id} />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </AsyncBody>
      </SheetContent>
    </Sheet>
  );
}

function CronRow({
  j,
  busy,
  onRun,
  onPause,
  onRuns,
}: {
  j: CronJobInfo;
  busy: string | null;
  onRun: () => void;
  onPause: () => void;
  onRuns: () => void;
}) {
  return (
    <TableRow data-testid="cron-row" data-name={j.name} data-paused={j.paused}>
      <TableCell className="max-w-xs">
        <p className="font-mono text-xs font-medium">{j.name}</p>
        <p className="line-clamp-2 text-xs text-muted-foreground">{j.description}</p>
        <p className="text-[11px] text-muted-foreground">
          agent <code>{j.agent}</code> → {j.deliver.join(", ") || "no delivery"}
        </p>
      </TableCell>
      <TableCell>
        <code className="text-xs">{j.schedule}</code>
        {j.tz && <p className="text-[11px] text-muted-foreground">{j.tz}</p>}
        {j.error && <p className="text-xs text-destructive">{j.error}</p>}
      </TableCell>
      <TableCell className="text-xs" data-testid="cron-next">
        {j.enabled && !j.paused ? fmtWhen(j.next_run_at) : "—"}
      </TableCell>
      <TableCell data-testid="cron-last">
        {j.last_run ? (
          <div className="space-y-0.5">
            <StatusBadge status={j.last_run.status} />
            <p className="text-[11px] whitespace-nowrap text-muted-foreground">{fmtWhen(j.last_run.scheduled_for)}</p>
          </div>
        ) : (
          <span className="text-xs text-muted-foreground">never</span>
        )}
      </TableCell>
      <TableCell>
        <div className="flex flex-wrap gap-1">
          {!j.enabled && <Badge variant="outline" title="enabled: false in the job file — runs only when fired manually">disabled</Badge>}
          {j.paused ? <Badge variant="warning">paused</Badge> : j.enabled && <Badge variant="success">active</Badge>}
        </div>
      </TableCell>
      <TableCell>
        <div className="flex flex-wrap items-center gap-1">
          <Button size="sm" onClick={onRun} disabled={busy !== null} aria-label={`Run ${j.name} now`}>
            <PlayIcon aria-hidden /> Run now
          </Button>
          <Button size="sm" variant="outline" onClick={onPause} disabled={busy !== null}>
            {j.paused ? <PlayIcon aria-hidden /> : <PauseIcon aria-hidden />} {j.paused ? "Resume" : "Pause"}
          </Button>
          <Button size="icon-sm" variant="ghost" onClick={onRuns} aria-label={`Runs of ${j.name}`} title="Run history">
            <HistoryIcon aria-hidden />
          </Button>
        </div>
      </TableCell>
    </TableRow>
  );
}

function CronTab({ tick }: { tick: number }) {
  const jobs = useAsync(() => api.cron(), [tick]);
  const action = useAction();
  const [runsOf, setRunsOf] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const run = async (name: string) => {
    const r = await action.run(`run:${name}`, () => api.cronAction(name, "run"));
    if (r) {
      setNotice(`Run #${r.run_id} of ${r.job} started (${r.status}).`);
      jobs.reload();
    }
  };
  const togglePause = async (j: CronJobInfo) => {
    const r = await action.run(`pause:${j.name}`, () => api.cronAction(j.name, j.paused ? "resume" : "pause"));
    if (r) jobs.setData((list) => list?.map((x) => (x.name === j.name ? { ...x, paused: r.paused } : x)));
  };

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <p className="flex-1 text-xs text-muted-foreground">
          Jobs from <code>jobs/&lt;name&gt;.yaml</code> (cron, <code>every</code> or one-shot). Disabled jobs can still be fired by hand.
        </p>
        <RefreshButton onClick={jobs.reload} busy={jobs.loading} label="Refresh jobs" />
      </div>
      <ErrorNote error={action.error} />
      {notice && (
        <p role="status" className="text-xs text-muted-foreground" data-testid="cron-notice">
          {notice}
        </p>
      )}
      <AsyncBody state={jobs} empty="No scheduled jobs" emptyHint="Add jobs/<name>.yaml to a definition root and reload the harness.">
        {(rows) => (
          <Table aria-label="Scheduled jobs">
            <TableHeader>
              <TableRow>
                <TableHead>Job</TableHead>
                <TableHead>Schedule</TableHead>
                <TableHead>Next run</TableHead>
                <TableHead>Last run</TableHead>
                <TableHead>State</TableHead>
                <TableHead>Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((j) => (
                <CronRow
                  key={j.name}
                  j={j}
                  busy={action.busy}
                  onRun={() => void run(j.name)}
                  onPause={() => void togglePause(j)}
                  onRuns={() => setRunsOf(j.name)}
                />
              ))}
            </TableBody>
          </Table>
        )}
      </AsyncBody>
      <RunsDrawer job={runsOf} tick={tick} onClose={() => setRunsOf(null)} />
    </div>
  );
}

function WebhookRow({ w, onDeliveries }: { w: WebhookRouteInfo; onDeliveries: () => void }) {
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
      <TableCell>
        <Button size="sm" variant="outline" onClick={onDeliveries} aria-label={`Deliveries of ${w.name}`}>
          <InboxIcon aria-hidden /> Deliveries
        </Button>
      </TableCell>
    </TableRow>
  );
}

function WebhooksTab({ tick }: { tick: number }) {
  const hooks = useAsync(() => api.webhooks(), []);
  const [drawer, setDrawer] = useState<string | null>(null);
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <p className="flex-1 text-xs text-muted-foreground">
          HMAC-verified ingress routes from <code>webhooks/&lt;route&gt;.yaml</code>. Secrets are never shown.
        </p>
        <RefreshButton onClick={hooks.reload} busy={hooks.loading} label="Refresh webhooks" />
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
                <TableHead>
                  <span className="sr-only">Actions</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((w) => (
                <WebhookRow key={w.name} w={w} onDeliveries={() => setDrawer(w.name)} />
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
    </div>
  );
}

export function JobsPage() {
  const [params, setParams] = useSearchParams();
  const raw = params.get("tab");
  const tab: Tab = (TABS as readonly string[]).includes(raw ?? "") ? (raw as Tab) : "cron";
  const [tick, setTick] = useState(0);
  // cron.* / delivery.* events refresh the tables live.
  useServerEvents(["cron.", "delivery."], () => setTick((t) => t + 1), true);

  return (
    <div className="flex flex-col">
      <PageHeader title="Jobs" description="Scheduled jobs, webhook routes and the deliveries outbox." />
      <div className="p-4">
        <Tabs value={tab} onValueChange={(v) => setParams(v === "cron" ? {} : { tab: v }, { replace: true })}>
          <TabsList aria-label="Jobs sections">
            <TabsTrigger value="cron">Cron</TabsTrigger>
            <TabsTrigger value="webhooks">Webhooks</TabsTrigger>
            <TabsTrigger value="deliveries">Deliveries</TabsTrigger>
          </TabsList>
          <TabsContent value="cron">
            <CronTab tick={tick} />
          </TabsContent>
          <TabsContent value="webhooks">
            <WebhooksTab tick={tick} />
          </TabsContent>
          <TabsContent value="deliveries">
            <DeliveriesPanel tick={tick} />
          </TabsContent>
        </Tabs>
      </div>
    </div>
  );
}
