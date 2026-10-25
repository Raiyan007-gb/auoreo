import type { CommandInfo, PluginInfo, ServerEvent } from "@sdk/client";
import { LinkIcon } from "lucide-react";
import { type FormEvent, useMemo, useState } from "react";
import { Link } from "react-router";
import { ErrorNote, PageHeader } from "@/components/AppShell";
import { AsyncBody, RefreshButton, StatusBadge } from "@/components/ops/AsyncBody";
import { useAction, useServerEvents } from "@/components/ops/opsUtils";
import { MenuPreview } from "@/components/platforms/MenuPreview";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label } from "@/components/ui/input";
import { useAsync } from "@/hooks/useAsync";
import { api } from "@/lib/api";
import { relTime } from "@/lib/utils";

interface Paired {
  readonly chat: string;
  readonly at: string;
}

/** Inline pairing: the bot shows a 6-digit code to an unknown chat; entering it allowlists the chat. */
function PairForm({ name, onPaired }: { name: string; onPaired: () => void }) {
  const [code, setCode] = useState("");
  const [done, setDone] = useState<string | null>(null);
  const action = useAction();
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const r = await action.run("pair", () => api.pairPlugin(name, code.trim()));
    if (r) {
      setDone(r.chat_id ? `Paired chat ${r.chat_id}` : "Paired");
      setCode("");
      onPaired();
    }
  };
  const id = `pair-${name}`;
  return (
    <form onSubmit={(e) => void submit(e)} className="space-y-1" data-testid="pair-form">
      <Label htmlFor={id}>Pairing code</Label>
      <div className="flex gap-2">
        <Input
          id={id}
          inputMode="numeric"
          autoComplete="one-time-code"
          pattern="[0-9]{6}"
          maxLength={6}
          value={code}
          onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
          placeholder="123456"
          className="w-28 font-mono"
        />
        <Button type="submit" size="sm" disabled={code.length !== 6 || action.busy !== null}>
          <LinkIcon aria-hidden /> Pair
        </Button>
      </div>
      <ErrorNote error={action.error} />
      {done && (
        <p role="status" className="text-xs text-success">
          {done}
        </p>
      )}
    </form>
  );
}

function PlatformCard({ p, paired, commands, onPaired }: { p: PluginInfo; paired: Paired[]; commands: CommandInfo[]; onPaired: () => void }) {
  return (
    <Card data-testid="platform-card" data-name={p.name}>
      <CardHeader>
        <div className="flex items-center gap-2">
          <CardTitle className="font-mono">{p.name}</CardTitle>
          <StatusBadge status={p.status} />
          <span className="text-xs text-muted-foreground">v{p.version}</span>
          <div className="ml-auto flex flex-wrap gap-1">
            {p.provides.map((x) => (
              <Badge key={x} variant="outline">
                {x}
              </Badge>
            ))}
          </div>
        </div>
        <CardDescription>{p.description}</CardDescription>
        {p.error && <p className="text-xs text-destructive">{p.error}</p>}
      </CardHeader>
      <CardContent className="grid gap-4 md:grid-cols-2">
        <div className="space-y-3">
          {p.provides.includes("connector") ? (
            <PairForm name={p.name} onPaired={onPaired} />
          ) : (
            <p className="text-xs text-muted-foreground">Delivery-only: no inbound chats to pair.</p>
          )}
          {p.provides.includes("connector") && (
          <div className="space-y-1">
            <p className="text-xs font-medium">Allowlist</p>
            {paired.length ? (
              <ul className="space-y-0.5" data-testid="allowlist">
                {paired.map((c) => (
                  <li key={c.chat} className="flex gap-2 font-mono text-xs">
                    {c.chat}
                    <span className="text-muted-foreground" title={c.at}>
                      paired {relTime(c.at)}
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-xs text-muted-foreground" data-testid="allowlist-empty">
                No chats paired from this server yet.
              </p>
            )}
            <p className="text-[11px] text-muted-foreground">
              Stored in <code>settings.json</code> → <code>plugins.{p.name}.allowlist</code> (plus any ids set there by hand).
            </p>
          </div>
          )}
        </div>
        <div className="space-y-1">
          <p className="text-xs font-medium">Menu preview</p>
          <MenuPreview commands={commands} platform={p.name} />
        </div>
      </CardContent>
    </Card>
  );
}

/** Spec §17 Messaging/Platforms: connector plugins with status, pairing, allowlist and menu preview. */
export function PlatformsPage() {
  const plugins = useAsync(() => api.plugins(), []);
  const commands = useAsync(() => api.commands(), []);
  const [paired, setPaired] = useState<Record<string, Paired[]>>({});
  useServerEvents(["plugin."], (e: ServerEvent) => {
    if (e.type === "plugin.paired") {
      const name = String(e.data.name ?? "");
      const chat = String(e.data.chat_id ?? "");
      if (!name || !chat) return;
      setPaired((m) => ({ ...m, [name]: [...(m[name] ?? []).filter((c) => c.chat !== chat), { chat, at: e.created_at }] }));
    } else plugins.reload();
  });
  const connectors = useMemo(
    () => ({ ...plugins, data: plugins.data?.filter((p) => p.provides.includes("connector") || p.provides.includes("delivery")) }),
    [plugins],
  );
  return (
    <div className="flex flex-col">
      <PageHeader
        title="Messaging"
        description={
          <>
            Chat platform connectors (Telegram, Discord, MQTT…). Start/stop and logs live on{" "}
            <Link to="/plugins" className="underline underline-offset-2">
              Plugins
            </Link>
            .
          </>
        }
        actions={<RefreshButton onClick={plugins.reload} busy={plugins.loading} label="Refresh platforms" />}
      />
      <div className="space-y-3 p-4">
        <AsyncBody state={connectors} empty="No messaging plugins" emptyHint="Add plugins/<name>/plugin.yaml with provides: [connector] or [delivery].">
          {(rows) =>
            rows.map((p) => (
              <PlatformCard key={p.name} p={p} paired={paired[p.name] ?? []} commands={commands.data ?? []} onPaired={plugins.reload} />
            ))
          }
        </AsyncBody>
      </div>
    </div>
  );
}
