import type { EnvApplied, EnvEntry } from "@sdk/env";
import { LockIcon, RotateCcwIcon } from "lucide-react";
import type * as React from "react";
import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input, NativeSelect, Textarea } from "@/components/ui/input";
import { api } from "@/lib/api";
import { errorText, relTime } from "@/lib/utils";
import { draftOf, parseDraft, SOURCE_LABEL, SOURCE_VARIANT } from "./envUtils";

export interface RowProps {
  readonly entry: EnvEntry;
  readonly onApplied: (r: EnvApplied) => void;
  readonly onError: (message: string) => void;
}

function Badges({ entry }: { entry: EnvEntry }) {
  return (
    <span className="flex flex-wrap gap-1">
      <Badge variant={SOURCE_VARIANT[entry.source]} data-testid={`env-source-${entry.name}`}>
        {SOURCE_LABEL[entry.source]}
      </Badge>
      {!entry.live && (
        <Badge variant="warning" title={entry.restart_reason ?? undefined}>
          Needs restart
        </Badge>
      )}
      {entry.pending_restart && <Badge variant="destructive">Waiting for restart</Badge>}
    </span>
  );
}

function ReadOnly({ entry }: { entry: EnvEntry }) {
  const shown = entry.kind === "secret" ? (entry.is_set ? "•••••• (set)" : "not set") : draftOf(entry) || "(unset)";
  return (
    <div className="grid gap-1" data-testid={`env-locked-${entry.name}`}>
      <code className="block max-h-24 overflow-auto rounded-lg border bg-muted/60 px-2.5 py-1.5 font-mono text-xs whitespace-pre-wrap text-muted-foreground">
        {shown}
      </code>
      <p className="flex items-center gap-1 text-xs text-muted-foreground">
        <LockIcon className="size-3" aria-hidden /> {entry.locked_reason}
      </p>
    </div>
  );
}

function SettingInput({ entry, onApplied, onError }: RowProps) {
  const current = draftOf(entry);
  const [draft, setDraft] = useState(current);
  const [seen, setSeen] = useState(current);
  const [busy, setBusy] = useState(false);
  if (seen !== current) {
    // the server value changed (save, live event): follow it
    setSeen(current);
    setDraft(current);
  }
  const parsed = parseDraft(entry, draft);
  const save = async (value: unknown) => {
    setBusy(true);
    try {
      onApplied(await api.envSet(entry.name, value));
    } catch (err) {
      onError(errorText(err));
    } finally {
      setBusy(false);
    }
  };
  const id = `env-input-${entry.name}`;
  if (entry.type === "bool") {
    const on = entry.value === true;
    return (
      <button
        type="button"
        role="switch"
        aria-checked={on}
        aria-label={entry.name}
        data-testid={id}
        disabled={busy}
        onClick={() => void save(!on)}
        className={`relative h-5 w-9 rounded-full transition-colors ${on ? "bg-brand" : "bg-muted-foreground/30"}`}
      >
        <span
          className={`absolute top-0.5 size-4 rounded-full bg-card transition-all ${on ? "left-4.5" : "left-0.5"}`}
        />
      </button>
    );
  }
  if (entry.type === "enum") {
    return (
      <NativeSelect
        aria-label={entry.name}
        data-testid={id}
        value={draft}
        disabled={busy}
        onChange={(e) => void save(e.target.value || null)}
      >
        {entry.nullable && <option value="">(unset)</option>}
        {(entry.choices ?? []).map((c) => (
          <option key={c} value={c}>
            {c}
          </option>
        ))}
      </NativeSelect>
    );
  }
  const Field = entry.type === "json" ? Textarea : Input;
  return (
    <form
      className="grid gap-1"
      onSubmit={(e) => {
        e.preventDefault();
        if (parsed.ok) void save(parsed.value);
      }}
    >
      <div className="flex items-start gap-2">
        <Field
          aria-label={entry.name}
          data-testid={id}
          className={entry.type === "json" ? "min-h-20 font-mono text-xs" : "font-mono"}
          type={entry.type === "int" || entry.type === "float" ? "number" : undefined}
          step={entry.type === "float" ? "any" : undefined}
          value={draft}
          placeholder={
            entry.default === null || entry.default === undefined ? "unset" : String(JSON.stringify(entry.default))
          }
          onChange={(e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => setDraft(e.target.value)}
        />
        <Button
          type="submit"
          size="sm"
          disabled={busy || !parsed.ok || draft === draftOf(entry)}
          data-testid={`env-save-${entry.name}`}
        >
          Save
        </Button>
      </div>
      {!parsed.ok && <p className="text-xs text-destructive">{parsed.error}</p>}
    </form>
  );
}

function SecretInput({ entry, onApplied, onError }: RowProps) {
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const run = async (fn: () => Promise<EnvApplied>) => {
    setBusy(true);
    try {
      onApplied(await fn());
      setValue("");
    } catch (err) {
      onError(errorText(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="grid gap-1">
      <div className="flex flex-wrap items-center gap-2">
        <Input
          type="password"
          autoComplete="new-password"
          aria-label={entry.name}
          data-testid={`env-input-${entry.name}`}
          className="max-w-xs flex-1 font-mono"
          placeholder={entry.is_set ? "•••••• (set)" : "not set"}
          value={value}
          onChange={(e) => setValue(e.target.value)}
        />
        <Button
          size="sm"
          disabled={busy || !value}
          data-testid={`env-save-${entry.name}`}
          onClick={() => void run(() => api.envSecretSet(entry.name, value))}
        >
          Save
        </Button>
        {entry.is_set && (
          <>
            <Button
              size="sm"
              variant="outline"
              disabled={busy || !value}
              data-testid={`env-rotate-${entry.name}`}
              onClick={() => void run(() => api.envSecretRotate(entry.name, value))}
            >
              Rotate
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={busy}
              data-testid={`env-delete-${entry.name}`}
              onClick={() => void run(() => api.envSecretDelete(entry.name))}
            >
              Delete
            </Button>
          </>
        )}
      </div>
      {entry.updated_at && <p className="text-xs text-muted-foreground">updated {relTime(entry.updated_at)}</p>}
    </div>
  );
}

const MODEL_FIELDS = new Set(["model", "small_model", "embed_model", "embed_dims"]);

export function EnvEntryRow(props: RowProps) {
  const { entry, onApplied, onError } = props;
  const resettable = entry.writable && entry.kind === "setting" && entry.source === "db";
  const body = !entry.writable ? (
    <ReadOnly entry={entry} />
  ) : entry.kind === "secret" ? (
    <SecretInput {...props} />
  ) : (
    <SettingInput {...props} />
  );
  return (
    <div
      className="grid gap-x-4 gap-y-2 border-t border-border-subtle px-5 py-3 first:border-t-0 sm:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]"
      data-testid={`env-row-${entry.name}`}
    >
      <div className="grid content-start gap-1">
        <div className="flex flex-wrap items-center gap-2">
          <code className="font-mono text-[12.5px] font-medium break-all">{entry.name}</code>
          <Badges entry={entry} />
        </div>
        <p className="text-xs text-muted-foreground">{entry.description}</p>
        {entry.field && MODEL_FIELDS.has(entry.field) && (
          <a href="#models" className="text-xs text-brand-ink underline" data-testid={`env-models-link-${entry.name}`}>
            Choose from your providers in the Models tab →
          </a>
        )}
      </div>
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">{body}</div>
        {resettable && (
          <Button
            size="icon-sm"
            variant="ghost"
            aria-label={`Reset ${entry.name} to default`}
            title="Reset to default"
            data-testid={`env-reset-${entry.name}`}
            onClick={() => api.envSet(entry.name, null).then(onApplied, (err: unknown) => onError(errorText(err)))}
          >
            <RotateCcwIcon aria-hidden />
          </Button>
        )}
      </div>
    </div>
  );
}
