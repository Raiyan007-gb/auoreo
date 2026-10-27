import type { ProviderConnection } from "@sdk/models";
import { ApiError } from "@sdk/client";
import { KeyRoundIcon, RefreshCwIcon, Trash2Icon } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import { cn, errorText } from "@/lib/utils";

const DOT: Record<ProviderConnection["status"], string> = {
  ok: "bg-success",
  invalid: "bg-destructive",
  error: "bg-warning",
  unchecked: "bg-muted-foreground",
};

function ago(ts: string | null | undefined): string {
  if (!ts) return "never";
  const s = Math.max(0, Math.round((Date.now() - new Date(ts).getTime()) / 1000));
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86_400) return `${Math.round(s / 3600)} h ago`;
  return new Date(ts).toLocaleDateString();
}

function Row({
  c,
  onDone,
  onError,
}: {
  c: ProviderConnection;
  onDone: (note: string) => void;
  onError: (message: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [replacing, setReplacing] = useState(false);
  const [key, setKey] = useState("");
  const run = async (fn: () => Promise<string>) => {
    setBusy(true);
    try {
      onDone(await fn());
    } catch (e) {
      onError(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  const remove = () =>
    run(async () => {
      try {
        await api.providerDelete(c.id);
      } catch (e) {
        if (!(e instanceof ApiError && e.status === 409)) throw e;
        if (!window.confirm(`${errorText(e)}\n\nRemove ${c.provider_name} anyway?`)) return "Kept";
        await api.providerDelete(c.id, true);
      }
      return `${c.provider_name} removed`;
    });
  return (
    <li className="grid gap-2 border-b py-2 last:border-0" data-testid={`provider-row-${c.provider}`}>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span
          className={cn("size-2.5 rounded-full", DOT[c.status])}
          data-testid={`provider-status-${c.provider}`}
          data-status={c.status}
          aria-label={c.status}
        />
        <span className="font-medium">{c.provider_name}</span>
        {c.label && c.label !== c.provider_name && <span className="text-muted-foreground">{c.label}</span>}
        <span className="tabular-nums text-muted-foreground" data-testid={`provider-count-${c.provider}`}>
          {c.model_count} models
        </span>
        <span className="text-xs text-muted-foreground">checked {ago(c.checked_at)}</span>
        <span className="ml-auto flex gap-1">
          <Button
            size="sm"
            variant="ghost"
            disabled={busy}
            title="Refresh the model list"
            data-testid={`provider-refresh-${c.provider}`}
            onClick={() =>
              void run(async () => {
                const r = await api.providerRefresh(c.id);
                if (!r.ok) throw new Error(`${c.provider_name}: ${r.error ?? r.status}`);
                return `${c.provider_name}: ${r.model_count} models`;
              })
            }
          >
            <RefreshCwIcon aria-hidden /> Refresh
          </Button>
          <Button size="sm" variant="ghost" disabled={busy} onClick={() => setReplacing((v) => !v)}>
            <KeyRoundIcon aria-hidden /> Replace key
          </Button>
          <Button
            size="sm"
            variant="ghost"
            disabled={busy}
            data-testid={`provider-remove-${c.provider}`}
            onClick={() => void remove()}
          >
            <Trash2Icon aria-hidden /> Remove
          </Button>
        </span>
      </div>
      {c.last_error && c.status !== "ok" && <p className="text-xs text-destructive">{c.last_error}</p>}
      {replacing && (
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            void run(async () => {
              const r = await api.providerUpdate(c.id, { key: key.trim() });
              if (!r.ok) throw new Error(`${c.provider_name}: ${r.error ?? r.status}`);
              setKey("");
              setReplacing(false);
              return `${c.provider_name}: key replaced (${r.model_count} models)`;
            });
          }}
        >
          <Input
            type="password"
            autoComplete="off"
            aria-label="New API key"
            placeholder="new key"
            value={key}
            onChange={(e) => setKey(e.target.value)}
          />
          <Button size="sm" type="submit" disabled={busy || !key.trim()}>
            Save
          </Button>
        </form>
      )}
    </li>
  );
}

export function ProviderList({
  connections,
  onDone,
  onError,
}: {
  connections: readonly ProviderConnection[];
  onDone: (note: string) => void;
  onError: (message: string) => void;
}) {
  if (connections.length === 0)
    return (
      <p className="text-sm text-muted-foreground" data-testid="providers-empty">
        No provider connected yet. Add one below.
      </p>
    );
  return (
    <ul data-testid="providers-list">
      {connections.map((c) => (
        <Row key={c.id} c={c} onDone={onDone} onError={onError} />
      ))}
    </ul>
  );
}
