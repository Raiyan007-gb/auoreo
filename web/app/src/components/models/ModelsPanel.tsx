import type { ModelSelection, ModelSelectionApplied, ProviderResult, SelectedModel } from "@sdk/models";
import { useCallback } from "react";
import { ErrorNote } from "@/components/AppShell";
import { Badge } from "@/components/ui/badge";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useAsync } from "@/hooks/useAsync";
import { api } from "@/lib/api";
import { useEvents } from "@/lib/eventsHub";
import { AddProvider } from "./AddProvider";
import { ModelPicker } from "./ModelPicker";
import { appliedNote, useToast } from "./modelsUtils";
import { ProviderList } from "./ProviderList";

const LIVE_TYPES = ["providers.changed", "models.changed", "settings.changed"];
const ROLE_LABEL: Record<SelectedModel["role"], string> = { main: "Main", small: "Small", embedding: "Embeddings" };

export function SelectionSummary({ selection }: { selection: ModelSelection | undefined }) {
  if (!selection) return null;
  const rows = [selection.main, selection.small, selection.embedding];
  return (
    <dl className="flex flex-wrap gap-3 text-sm" data-testid="model-selection">
      {rows.map((s) => (
        <div key={s.role} className="flex min-w-0 flex-[1_1_220px] flex-col gap-1 rounded-[10px] border border-border-subtle bg-muted/50 px-3.5 py-3">
          <dt className="text-xs text-muted-foreground">
            {ROLE_LABEL[s.role]}
            {s.role === "embedding" && " · changing it needs a restart"}
          </dt>
          <dd className="flex flex-wrap items-center gap-2" data-testid={`selected-${s.role}`}>
            {s.value ? (
              <code className="font-mono text-ui font-medium break-all">{s.value}</code>
            ) : (
              <span className="text-muted-foreground">{s.role === "small" ? "same as main" : "not set"}</span>
            )}
            {s.provider && <Badge variant="info">{s.provider}</Badge>}
            {s.source === "env" && <Badge variant="warning">set in the real environment</Badge>}
            {s.source === "override" && <Badge variant="warning">startup flag</Badge>}
            {s.source === "default" && s.value && <Badge variant="outline">default</Badge>}
          </dd>
        </div>
      ))}
    </dl>
  );
}

/** Settings → Models: connected providers, add one with a key, pick the main / small / embedding model. */
export function ModelsPanel() {
  const providers = useAsync(() => api.providers(), []);
  const selection = useAsync(() => api.modelSelection(), []);
  const [toast, show] = useToast();
  const { reload: reloadProviders } = providers;
  const { reload: reloadSelection } = selection;
  const reload = useCallback(() => {
    reloadProviders();
    reloadSelection();
  }, [reloadProviders, reloadSelection]);

  useEvents({ channel: "server", afterSeq: "live", types: LIVE_TYPES }, (e) => {
    if (LIVE_TYPES.includes(e.type)) reload();
  });

  const onConnected = (r: ProviderResult) => {
    show(`${r.connection?.provider_name ?? "Provider"} connected — ${r.model_count} models`);
    reload();
  };
  const onApplied = (r: ModelSelectionApplied) => {
    show(appliedNote(r));
    selection.setData(() => r.selection);
  };
  const onError = (m: string) => show(m, true);
  const connections = providers.data?.connections ?? [];
  // A model in use with no connection behind it runs on a key from the Environment tab or the real env.
  const mainModel = selection.data?.main.value ?? "";
  const external = (() => {
    if (connections.length > 0 || !mainModel) return null;
    const p = providers.data?.providers.find((x) => mainModel.startsWith(x.prefix));
    return p && !p.needs_base_url && !p.manual_models ? { id: p.id, name: p.name } : null;
  })();

  return (
    <div className="grid gap-5" data-testid="models-panel">
      <Card className="gap-3.5">
        <CardHeader>
          <CardTitle>Models in use</CardTitle>
          <CardDescription>What the agent uses from its next step. Change it below — no restart needed.</CardDescription>
        </CardHeader>
        <SelectionSummary selection={selection.data} />
        {toast && (
          <p role="status" data-testid="models-toast" className={`text-sm ${toast.error ? "text-destructive" : "text-success-ink"}`}>
            {toast.text}
          </p>
        )}
        <ErrorNote error={selection.error ?? providers.error} />
      </Card>
      <div className="flex flex-wrap items-start gap-5">
        <section aria-labelledby="providers-h" className="flex min-w-0 flex-[1_1_420px] flex-col gap-3">
          <h2 id="providers-h" className="text-title font-semibold">
            Connected providers
          </h2>
          <ProviderList
            connections={connections}
            external={external}
            onDone={(note) => {
              show(note);
              reload();
            }}
            onError={onError}
          />
        </section>
        <Card className="min-w-0 flex-[1_1_340px] gap-3">
          <CardHeader>
            <CardTitle>Add a provider</CardTitle>
            <CardDescription className="text-ui">
              Paste an API key. We recognise the provider and check the key by listing your models before saving
              anything.
            </CardDescription>
          </CardHeader>
          <AddProvider compact specs={providers.data?.providers ?? []} onConnected={onConnected} />
        </Card>
      </div>
      <Card className="gap-3.5">
        <CardHeader>
          <CardTitle>Choose a model</CardTitle>
          <CardDescription>
            Models available to each connected key, refreshed hourly or on demand. Changes apply to the next turn of
            every session.
          </CardDescription>
        </CardHeader>
        <ModelPicker connections={connections} onApplied={onApplied} onError={onError} />
      </Card>
    </div>
  );
}
