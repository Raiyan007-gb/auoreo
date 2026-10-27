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
    <dl className="grid gap-1 text-sm sm:grid-cols-[8rem_1fr]" data-testid="model-selection">
      {rows.map((s) => (
        <div key={s.role} className="contents">
          <dt className="text-muted-foreground">{ROLE_LABEL[s.role]}</dt>
          <dd className="flex flex-wrap items-center gap-2" data-testid={`selected-${s.role}`}>
            {s.value ? (
              <code className="font-mono text-xs">{s.value}</code>
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

  return (
    <div className="grid gap-4" data-testid="models-panel">
      <Card>
        <CardHeader>
          <CardTitle>Current models</CardTitle>
          <CardDescription>What the agent uses from its next step. Change it below — no restart needed.</CardDescription>
        </CardHeader>
        <SelectionSummary selection={selection.data} />
        {toast && (
          <p role="status" data-testid="models-toast" className={`text-sm ${toast.error ? "text-destructive" : "text-success"}`}>
            {toast.text}
          </p>
        )}
        <ErrorNote error={selection.error ?? providers.error} />
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Providers</CardTitle>
          <CardDescription>
            Paste an API key: the provider is detected from it, the key is validated by listing your models and kept in
            the encrypted secret store.
          </CardDescription>
        </CardHeader>
        <ProviderList
          connections={connections}
          onDone={(note) => {
            show(note);
            reload();
          }}
          onError={onError}
        />
        <div className="border-t pt-3">
          <h4 className="mb-2 text-sm font-medium">Add provider</h4>
          <AddProvider specs={providers.data?.providers ?? []} onConnected={onConnected} />
        </div>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Choose models</CardTitle>
          <CardDescription>Models available to each connected key, refreshed hourly or on demand.</CardDescription>
        </CardHeader>
        <ModelPicker connections={connections} onApplied={onApplied} onError={onError} />
      </Card>
    </div>
  );
}
