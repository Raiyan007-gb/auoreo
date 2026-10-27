import type { ModelRole, ModelSelectionApplied, ProviderConnection, ProviderModel } from "@sdk/models";
import { RefreshCwIcon } from "lucide-react";
import { useMemo, useState } from "react";
import { ErrorNote } from "@/components/AppShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input, NativeSelect } from "@/components/ui/input";
import { useAsync } from "@/hooks/useAsync";
import { api } from "@/lib/api";
import { errorText } from "@/lib/utils";

const ROLES: { role: ModelRole; label: string; kinds: readonly ProviderModel["kind"][] }[] = [
  { role: "main", label: "Use as main", kinds: ["chat"] },
  { role: "small", label: "Use as small", kinds: ["chat"] },
  { role: "embedding", label: "Use for embeddings", kinds: ["embedding"] },
];

function ctx(n: number | null | undefined): string {
  if (!n) return "";
  return n >= 1_000_000 ? `${(n / 1_000_000).toFixed(n % 1_000_000 ? 1 : 0)}M ctx` : `${Math.round(n / 1000)}k ctx`;
}

function price(m: ProviderModel): string {
  if (m.input_price == null && m.output_price == null) return "";
  const f = (v: number | null | undefined) => (v == null ? "?" : `$${v < 1 ? v.toFixed(2) : v.toFixed(v % 1 ? 2 : 0)}`);
  return `${f(m.input_price)} / ${f(m.output_price)} per 1M`;
}

/**
 * Provider (connected only) → searchable model list → "Use as main / small / embeddings".
 * Embedding models are listed too; only the embeddings button applies to them.
 */
export function ModelPicker({
  connections,
  roles = ["main", "small", "embedding"],
  onApplied,
  onError,
}: {
  connections: readonly ProviderConnection[];
  roles?: readonly ModelRole[];
  onApplied: (r: ModelSelectionApplied) => void;
  onError: (message: string) => void;
}) {
  const [picked, setConnId] = useState<number | null>(null);
  const connId = connections.some((c) => c.id === picked) ? picked : (connections.at(-1)?.id ?? null);
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState<"" | ProviderModel["kind"]>("");
  const [busy, setBusy] = useState(false);

  const count = connections.find((c) => c.id === connId)?.model_count;
  const models = useAsync(
    () => (connId === null ? Promise.resolve(undefined) : api.providerModels(connId)),
    [connId, count],
  );
  const refresh = () => {
    if (connId === null) return;
    api.providerModels(connId, { refresh: true }).then(
      (d) => models.setData(() => d),
      (e: unknown) => onError(errorText(e)),
    );
  };
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (models.data?.models ?? []).filter(
      (m) => (!kind || m.kind === kind) && (!q || m.model_id.toLowerCase().includes(q) || m.name.toLowerCase().includes(q)),
    );
  }, [models.data, query, kind]);

  const use = async (role: ModelRole, m: ProviderModel) => {
    if (connId === null) return;
    setBusy(true);
    try {
      onApplied(await api.modelSelect({ [role]: { connection_id: connId, model_id: m.model_id } }));
    } catch (e) {
      onError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  if (connections.length === 0) return <p className="text-sm text-muted-foreground">Connect a provider first.</p>;
  return (
    <div className="grid gap-3" data-testid="model-picker">
      <div className="flex flex-wrap items-center gap-2">
        <NativeSelect
          aria-label="Provider"
          data-testid="model-provider"
          className="max-w-xs"
          value={connId ?? ""}
          onChange={(e) => setConnId(Number(e.target.value))}
        >
          {connections.map((c) => (
            <option key={c.id} value={c.id}>
              {c.label && c.label !== c.provider_name ? `${c.provider_name} — ${c.label}` : c.provider_name}
            </option>
          ))}
        </NativeSelect>
        <Input
          aria-label="Search models"
          data-testid="model-search"
          className="max-w-xs"
          placeholder="Search models…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <NativeSelect
          aria-label="Kind"
          className="w-36"
          value={kind}
          onChange={(e) => setKind(e.target.value as typeof kind)}
        >
          <option value="">All kinds</option>
          <option value="chat">Chat</option>
          <option value="embedding">Embedding</option>
          <option value="other">Other</option>
        </NativeSelect>
        <Button size="sm" variant="ghost" onClick={refresh}>
          <RefreshCwIcon aria-hidden /> Refresh list
        </Button>
        <span className="text-xs text-muted-foreground">
          {shown.length} of {models.data?.models.length ?? 0}
          {models.data?.stale ? " · stale" : ""}
        </span>
      </div>
      <ErrorNote error={models.error} />
      {models.data?.error && models.data.status !== "ok" && (
        <p className="text-xs text-destructive">Last refresh failed: {models.data.error}</p>
      )}
      <ul className="grid max-h-120 overflow-auto rounded-lg border" data-testid="model-list">
        {shown.map((m) => (
          <li key={m.model_id} className="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-b border-border-subtle px-4 py-2.5 text-sm last:border-0 hover:bg-muted/40" data-testid={`model-row-${m.model_id}`}>
            <code className="font-mono text-[13px] font-medium">{m.model_id}</code>
            {m.name !== m.model_id && <span className="text-muted-foreground">{m.name}</span>}
            <Badge variant={m.kind === "chat" ? "info" : m.kind === "embedding" ? "success" : "outline"}>{m.kind}</Badge>
            {ctx(m.context_window) && <span className="text-xs tabular-nums text-muted-foreground">{ctx(m.context_window)} context</span>}
            {price(m) && <span className="text-xs tabular-nums text-muted-foreground">{price(m)}</span>}
            <span className="ml-auto flex gap-1">
              {ROLES.filter((r) => roles.includes(r.role) && r.kinds.includes(m.kind)).map((r) => (
                <Button
                  key={r.role}
                  size="sm"
                  variant="outline"
                  disabled={busy}
                  data-testid={`use-${r.role}-${m.model_id}`}
                  onClick={() => void use(r.role, m)}
                >
                  {r.label}
                </Button>
              ))}
            </span>
          </li>
        ))}
        {shown.length === 0 && <li className="px-4 py-4 text-sm text-muted-foreground">No models match.</li>}
      </ul>
    </div>
  );
}
