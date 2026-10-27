import type { DotenvImport, EnvApplied } from "@sdk/env";
import { FileInputIcon, Undo2Icon } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ErrorNote } from "@/components/AppShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { useAsync } from "@/hooks/useAsync";
import { api } from "@/lib/api";
import { useEvents } from "@/lib/eventsHub";
import { errorText } from "@/lib/utils";
import { CustomVarsCard } from "./CustomVarsCard";
import { EnvEntryRow } from "./EnvEntryRow";
import { matches } from "./envUtils";

const LIVE_TYPES = ["settings.changed", "secret.changed"];

interface Toast {
  readonly text: string;
  readonly error: boolean;
}

function useToast(): [Toast | null, (text: string, error?: boolean) => void] {
  const [toast, setToast] = useState<Toast | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const show = useCallback((text: string, error = false) => {
    setToast({ text, error });
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setToast(null), 8000);
  }, []);
  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );
  return [toast, show];
}

function ImportDialog({
  plan,
  onClose,
  onConfirm,
}: {
  plan: DotenvImport | null;
  onClose: () => void;
  onConfirm: () => void;
}) {
  const moving = (plan?.moved ?? []).filter((m) => m.to === "secrets" || m.to === "settings");
  const staying = (plan?.moved ?? []).filter((m) => m.to !== "secrets" && m.to !== "settings");
  return (
    <Dialog open={plan !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent data-testid="import-dialog">
        <DialogTitle>Import existing .env</DialogTitle>
        <DialogDescription>
          One-time move of {plan?.files?.join(" and ") || "your .env"}: keys go to the encrypted secrets store (provider
          keys also become connections in the Models tab), AUOREO_* settings to the database. Security knobs are never
          imported. AUOREO never reads .env files — delete them afterwards.
        </DialogDescription>
        {plan && plan.moved.length === 0 && <p className="text-sm">{plan.note}</p>}
        <ul className="grid max-h-64 gap-1 overflow-auto text-sm" data-testid="import-plan">
          {moving.map((m) => (
            <li key={m.name} className="flex items-center gap-2">
              <code className="font-mono">{m.name}</code> →{" "}
              <Badge variant={m.to === "secrets" ? "success" : "info"}>
                {m.to === "secrets" ? "secrets.db" : "database"}
              </Badge>
              {m.provider && <Badge variant="outline">→ {m.provider} connection</Badge>}
            </li>
          ))}
          {staying.map((m) => (
            <li key={m.name} className="flex items-center gap-2 text-muted-foreground">
              <code className="font-mono">{m.name}</code> <Badge variant="outline">{m.to}</Badge>{" "}
              <span className="text-xs">{m.reason}</span>
            </li>
          ))}
        </ul>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button disabled={moving.length === 0} onClick={onConfirm} data-testid="import-confirm">
            Move {moving.length} value{moving.length === 1 ? "" : "s"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function EnvPanel() {
  const env = useAsync(() => api.envList(), []);
  const [query, setQuery] = useState("");
  const [onlySet, setOnlySet] = useState(false);
  const [plan, setPlan] = useState<DotenvImport | null>(null);
  const [toast, show] = useToast();
  const { reload } = env;
  const mine = useRef(0);

  const onApplied = useCallback(
    (r: EnvApplied) => {
      mine.current = Date.now();
      show(r.note);
      reload();
    },
    [reload, show],
  );
  const onError = useCallback((message: string) => show(message, true), [show]);

  // Live: writes from elsewhere (CLI `auoreo config set`, another tab) refresh the page.
  useEvents({ channel: "server", afterSeq: "live", types: LIVE_TYPES }, (e) => {
    if (!LIVE_TYPES.includes(e.type)) return;
    reload();
    const data = e.data as { source?: string; restart_required?: string[] };
    if (Date.now() - mine.current > 1500 && e.type === "settings.changed") {
      const restart = data.restart_required ?? [];
      show(
        restart.length
          ? `Saved — restart required for: ${restart.join(", ")}`
          : "Applied — the agent uses it from its next step",
      );
    }
  });

  const sections = useMemo(
    () =>
      (env.data?.sections ?? [])
        .map((s) => ({ ...s, entries: s.entries.filter((e) => e.field !== "env" && matches(e, query, onlySet)) }))
        .filter((s) => s.entries.length > 0),
    [env.data, query, onlySet],
  );

  return (
    <div className="grid gap-4" data-testid="env-panel">
      <Card>
        <CardHeader>
          <CardTitle>Environment</CardTitle>
          <CardDescription>
            Every variable AUOREO reads. Changes apply live unless marked “restart required”. Values from the real
            environment always win and are read-only here; secrets are write-only.
          </CardDescription>
        </CardHeader>
        <div className="flex flex-wrap items-center gap-2">
          <Input
            aria-label="Search variables"
            data-testid="env-search"
            className="max-w-xs"
            placeholder="Search…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <label className="flex items-center gap-1.5 text-sm">
            <input
              type="checkbox"
              className="size-4 accent-primary"
              data-testid="env-only-set"
              checked={onlySet}
              onChange={(e) => setOnlySet(e.target.checked)}
            />
            Only set
          </label>
          <span className="ml-auto flex gap-2">
            <Button
              size="sm"
              variant="outline"
              data-testid="env-import"
              disabled={!env.data?.dotenv_exists}
              title={env.data?.dotenv_paths?.join(", ") || "no .env file found"}
              onClick={() => api.envImportDotenv(true).then(setPlan, (err: unknown) => onError(errorText(err)))}
            >
              <FileInputIcon aria-hidden /> Import existing .env
            </Button>
            <Button
              size="sm"
              variant="outline"
              data-testid="env-undo"
              disabled={!env.data?.history}
              onClick={() => api.envUndo().then(onApplied, (err: unknown) => onError(errorText(err)))}
            >
              <Undo2Icon aria-hidden /> Undo last change
            </Button>
          </span>
        </div>
        {toast && (
          <p
            role="status"
            data-testid="env-toast"
            className={`text-sm ${toast.error ? "text-destructive" : "text-success"}`}
          >
            {toast.text}
          </p>
        )}
        {(env.data?.pending_restart?.length ?? 0) > 0 && (
          <p className="text-xs text-warning" data-testid="env-pending">
            Waiting for a restart: {env.data?.pending_restart?.join(", ")}
          </p>
        )}
        <ErrorNote error={env.error} />
      </Card>

      {env.data && <CustomVarsCard vars={env.data.custom} onApplied={onApplied} onError={onError} />}

      {sections.map((s) => (
        <Card key={s.id} data-testid={`env-section-${s.id}`}>
          <CardHeader>
            <CardTitle>{s.label}</CardTitle>
          </CardHeader>
          <div>
            {s.entries.map((e) => (
              <EnvEntryRow key={e.name} entry={e} onApplied={onApplied} onError={onError} />
            ))}
          </div>
        </Card>
      ))}

      <ImportDialog
        plan={plan}
        onClose={() => setPlan(null)}
        onConfirm={() =>
          api.envImportDotenv(false).then(
            (r) => {
              setPlan(null);
              const conns = (r.providers ?? []).map((p) => `${p.provider} ${p.ok ? "connected" : `failed (${p.error ?? ""})`}`);
              show(conns.length ? `${r.note} Providers: ${conns.join("; ")}.` : r.note);
              reload();
            },
            (err: unknown) => onError(errorText(err)),
          )
        }
      />
    </div>
  );
}
