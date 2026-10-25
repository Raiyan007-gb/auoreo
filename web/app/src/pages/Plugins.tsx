import type { HarnessEntry, PluginInfo, PluginSkillInfo, ServerEvent } from "@sdk/client";
import { BookOpenIcon, LinkIcon, PlayIcon, RefreshCwIcon, RotateCwIcon, SquareIcon } from "lucide-react";
import { type FormEvent, useState } from "react";
import { EmptyState, ErrorNote, PageHeader } from "@/components/AppShell";
import { AsyncBody, Loading, RefreshButton, StatusBadge } from "@/components/ops/AsyncBody";
import { useAction, useServerEventLog } from "@/components/ops/opsUtils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogTitle } from "@/components/ui/dialog";
import { Input, Label } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Tip } from "@/components/ui/tooltip";
import { useAsync } from "@/hooks/useAsync";
import { api } from "@/lib/api";
import { errorText, relTime } from "@/lib/utils";

const VERIFY_VARIANT = { ok: "success", mismatch: "destructive", missing: "warning" } as const;
const VERIFY_HINT = {
  ok: "lock.json matches the plugin folder",
  mismatch: "plugin folder changed since lock.json was written — reload reinstalls it",
  missing: "no lock.json yet — written on first install",
} as const;

function PluginSkillsDialog({ name, onClose }: { name: string | null; onClose: () => void }) {
  const skills = useAsync<PluginSkillInfo[] | undefined>(
    () => (name ? api.pluginSkills(name) : Promise.resolve(undefined)),
    [name],
  );
  return (
    <Dialog open={name !== null} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-2xl">
        <DialogTitle>Skills provided by {name}</DialogTitle>
        <DialogDescription>Plugin skills are offered to agents alongside the folder skills.</DialogDescription>
        {skills.error ? (
          <ErrorNote error={skills.error} />
        ) : skills.loading || !skills.data ? (
          <Loading />
        ) : skills.data.length === 0 ? (
          <EmptyState title="This plugin provides no skills" />
        ) : (
          <ul className="max-h-[50vh] space-y-2 overflow-auto">
            {skills.data.map((s) => (
              <li key={s.name} className="rounded-md border p-2">
                <p className="font-mono text-xs font-medium">{s.name}</p>
                <p className="text-xs text-muted-foreground">{s.description}</p>
              </li>
            ))}
          </ul>
        )}
      </DialogContent>
    </Dialog>
  );
}

function PairDialog({ name, onClose }: { name: string | null; onClose: () => void }) {
  const [code, setCode] = useState("");
  const [result, setResult] = useState<string | null>(null);
  const action = useAction();
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!name) return;
    const r = await action.run("pair", () => api.pairPlugin(name, code.trim()));
    if (r) setResult(r.chat_id ? `Paired chat ${r.chat_id}` : "Paired");
  };
  const close = () => {
    setCode("");
    setResult(null);
    action.clearError();
    onClose();
  };
  return (
    <Dialog open={name !== null} onOpenChange={(o) => !o && close()}>
      <DialogContent>
        <DialogTitle>Pair a chat with {name}</DialogTitle>
        <DialogDescription>
          Message the bot from the chat you want to allow; it replies with a 6-digit pairing code. Enter it here to
          add that chat to the allowlist.
        </DialogDescription>
        <form onSubmit={(e) => void submit(e)} className="space-y-2">
          <Label htmlFor="pair-code">Pairing code</Label>
          <Input
            id="pair-code"
            inputMode="numeric"
            autoComplete="one-time-code"
            pattern="[0-9]{6}"
            maxLength={6}
            value={code}
            onChange={(e) => setCode(e.target.value)}
            placeholder="123456"
          />
          <ErrorNote error={action.error} />
          {result && (
            <p role="status" className="text-sm text-success">
              {result}
            </p>
          )}
          <DialogFooter>
            <Button type="submit" disabled={code.trim().length !== 6 || action.busy !== null}>
              Pair
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function PluginRow({
  p,
  busy,
  onAction,
  onSkills,
  onPair,
}: {
  p: PluginInfo;
  busy: string | null;
  onAction: (name: string, a: "start" | "stop" | "reload") => void;
  onSkills: (name: string) => void;
  onPair: (name: string) => void;
}) {
  const running = p.status === "running";
  const mine = busy?.endsWith(`:${p.name}`) ?? false;
  return (
    <TableRow data-testid="plugin-row" data-name={p.name} data-status={p.status}>
      <TableCell className="max-w-md">
        <p className="font-mono text-xs font-medium">
          {p.name} <span className="text-muted-foreground">v{p.version}</span>
        </p>
        <p className="line-clamp-2 text-xs text-muted-foreground">{p.description}</p>
        {p.error && <p className="mt-1 text-xs text-destructive">{p.error}</p>}
      </TableCell>
      <TableCell>
        <StatusBadge status={p.status} />
      </TableCell>
      <TableCell>
        <div className="flex flex-wrap gap-1">
          {p.provides.map((x) => (
            <Badge key={x} variant="outline">
              {x}
            </Badge>
          ))}
        </div>
        {p.tools.length > 0 && <p className="mt-1 text-xs text-muted-foreground">{p.tools.length} tools</p>}
      </TableCell>
      <TableCell>
        <Tip label={VERIFY_HINT[p.verify]}>
          <Badge variant={VERIFY_VARIANT[p.verify]} tabIndex={0}>
            {p.verify}
          </Badge>
        </Tip>
      </TableCell>
      <TableCell>
        <div className="flex items-center gap-1">
          {running ? (
            <Button size="sm" variant="outline" disabled={busy !== null} onClick={() => onAction(p.name, "stop")}>
              <SquareIcon aria-hidden /> Stop
            </Button>
          ) : (
            <Button size="sm" variant="outline" disabled={busy !== null} onClick={() => onAction(p.name, "start")}>
              <PlayIcon aria-hidden /> Start
            </Button>
          )}
          <Button
            size="icon-sm"
            variant="ghost"
            aria-label={`Reload ${p.name}`}
            title={`Reload ${p.name}`}
            disabled={busy !== null}
            onClick={() => onAction(p.name, "reload")}
          >
            <RefreshCwIcon aria-hidden className={mine ? "animate-spin" : undefined} />
          </Button>
          <Button size="icon-sm" variant="ghost" aria-label={`Skills of ${p.name}`} title="Plugin skills" onClick={() => onSkills(p.name)}>
            <BookOpenIcon aria-hidden />
          </Button>
          {p.provides.includes("connector") && (
            <Button size="icon-sm" variant="ghost" aria-label={`Pair a chat with ${p.name}`} title="Pair a chat" onClick={() => onPair(p.name)}>
              <LinkIcon aria-hidden />
            </Button>
          )}
        </div>
      </TableCell>
    </TableRow>
  );
}

function PluginsTab() {
  const plugins = useAsync(() => api.plugins(), []);
  const action = useAction();
  const [skillsOf, setSkillsOf] = useState<string | null>(null);
  const [pairWith, setPairWith] = useState<string | null>(null);
  const onAction = async (name: string, a: "start" | "stop" | "reload") => {
    const row = await action.run(`${a}:${name}`, () => api.pluginAction(name, a));
    if (row) plugins.setData((list) => list?.map((p) => (p.name === name ? row : p)));
  };
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <p className="flex-1 text-xs text-muted-foreground">
          Out-of-process plugins (own venv, JSON-RPC over stdio). Enable one in <code>settings.plugins</code>, then
          start it here.
        </p>
        <RefreshButton onClick={plugins.reload} busy={plugins.loading} label="Refresh plugins" />
      </div>
      <ErrorNote error={action.error} />
      <AsyncBody state={plugins} empty="No plugins installed" emptyHint="Add plugins/<name>/plugin.yaml to a definition root.">
        {(rows) => (
          <Table aria-label="Plugins">
            <TableHeader>
              <TableRow>
                <TableHead>Plugin</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Provides</TableHead>
                <TableHead>Lock</TableHead>
                <TableHead>Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((p) => (
                <PluginRow
                  key={p.name}
                  p={p}
                  busy={action.busy}
                  onAction={(n, a) => void onAction(n, a)}
                  onSkills={setSkillsOf}
                  onPair={setPairWith}
                />
              ))}
            </TableBody>
          </Table>
        )}
      </AsyncBody>
      <PluginSkillsDialog name={skillsOf} onClose={() => setSkillsOf(null)} />
      <PairDialog name={pairWith} onClose={() => setPairWith(null)} />
    </div>
  );
}

function McpTab() {
  const state = useAsync(async () => {
    const [servers, tools] = await Promise.all([api.harnessKind("mcp"), api.harnessKind("tools")]);
    return { servers, tools };
  }, []);
  const toolsOf = (tools: HarnessEntry[], server: string) =>
    tools.filter((t) => t.name.startsWith(`mcp__${server}__`)).length;
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <p className="flex-1 text-xs text-muted-foreground">
          MCP servers from <code>mcp/&lt;name&gt;.yaml</code>; their tools register as <code>mcp__&lt;server&gt;__&lt;tool&gt;</code>.
        </p>
        <RefreshButton onClick={state.reload} busy={state.loading} label="Refresh MCP servers" />
      </div>
      <AsyncBody state={state} empty="No MCP servers configured" isEmpty={(d) => d.servers.length === 0}>
        {({ servers, tools }) => (
          <Table aria-label="MCP servers">
            <TableHeader>
              <TableRow>
                <TableHead>Server</TableHead>
                <TableHead>Transport</TableHead>
                <TableHead>Tools</TableHead>
                <TableHead>Source</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {servers.map((s) => (
                <TableRow key={s.name} data-testid="mcp-row" data-name={s.name}>
                  <TableCell>
                    <p className="font-mono text-xs font-medium">{s.name}</p>
                    <p className="font-mono text-[11px] break-all text-muted-foreground">{s.path}</p>
                  </TableCell>
                  <TableCell>
                    <Badge variant="outline">{s.description || "—"}</Badge>
                  </TableCell>
                  <TableCell className="text-xs">{toolsOf(tools, s.name) || "not connected"}</TableCell>
                  <TableCell>
                    <Badge>{s.source}</Badge>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </AsyncBody>
    </div>
  );
}

function describe(e: ServerEvent): string {
  const d = e.data;
  if (e.type === "harness.reloaded") {
    const changes = Object.entries(d)
      .filter(([k, v]) => !["generation", "errors"].includes(k) && v && typeof v === "object")
      .flatMap(([kind, v]) =>
        Object.entries(v as Record<string, string[]>).flatMap(([op, names]) =>
          Array.isArray(names) && names.length ? [`${kind} ${op}: ${names.join(", ")}`] : [],
        ),
      );
    const errs = Array.isArray(d.errors) ? d.errors.length : 0;
    return `generation ${String(d.generation)}${changes.length ? ` · ${changes.join("; ")}` : " · no changes"}${errs ? ` · ${errs} errors` : ""}`;
  }
  return [d.name, d.status, d.error ?? d.stderr].filter(Boolean).map(String).join(" · ");
}

function ReloadLog() {
  const log = useServerEventLog(["harness.reloaded", "plugin."]);
  return (
    <Card className="gap-2" data-testid="reload-log">
      <CardTitle className="text-sm">Hot-reload log</CardTitle>
      <CardDescription>harness.reloaded and plugin lifecycle events, newest first (live).</CardDescription>
      {log.length === 0 ? (
        <p className="text-xs text-muted-foreground">No reloads yet.</p>
      ) : (
        <ol className="max-h-64 space-y-1 overflow-auto text-xs" aria-live="polite">
          {log.map((e) => (
            <li key={e.seq} data-testid="reload-log-entry" data-type={e.type} className="flex gap-2">
              <span className="w-16 shrink-0 text-muted-foreground" title={e.created_at}>
                {relTime(e.created_at)}
              </span>
              <Badge variant={e.type === "harness.reloaded" ? "info" : "outline"} className="font-mono">
                {e.type}
              </Badge>
              <span className="min-w-0 break-words">{describe(e)}</span>
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}

export function PluginsPage() {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [tab, setTab] = useState("plugins");
  const [nonce, setNonce] = useState(0);
  const hotReload = async () => {
    setBusy(true);
    setErr(null);
    try {
      await api.reload();
      setNonce((n) => n + 1); // remount the tables so they refetch
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="flex flex-col">
      <PageHeader
        title="Plugins & MCP"
        description="Plugin catalog and MCP servers. Skills have their own page."
        actions={
          <Button variant="outline" onClick={() => void hotReload()} disabled={busy}>
            <RotateCwIcon aria-hidden className={busy ? "animate-spin" : undefined} /> Hot reload
          </Button>
        }
      />
      <div className="grid gap-4 p-4 xl:grid-cols-[1fr_22rem]">
        <div className="min-w-0 space-y-3">
          <ErrorNote error={err} />
          <Tabs value={tab} onValueChange={setTab}>
            <TabsList aria-label="Plugins and MCP">
              <TabsTrigger value="plugins">Plugins</TabsTrigger>
              <TabsTrigger value="mcp">MCP servers</TabsTrigger>
            </TabsList>
            <TabsContent value="plugins">
              <PluginsTab key={nonce} />
            </TabsContent>
            <TabsContent value="mcp">
              <McpTab key={nonce} />
            </TabsContent>
          </Tabs>
        </div>
        <ReloadLog />
      </div>
    </div>
  );
}
