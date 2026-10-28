import type { CustomVar, CustomVarWrite, EnvApplied } from "@sdk/env";
import { PencilIcon, Trash2Icon } from "lucide-react";
import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label } from "@/components/ui/input";
import { api } from "@/lib/api";
import { errorText } from "@/lib/utils";

interface Props {
  readonly vars: readonly CustomVar[];
  readonly onApplied: (r: EnvApplied) => void;
  readonly onError: (message: string) => void;
}

type Flags = Pick<CustomVarWrite, "secret" | "expose_to_shell" | "expose_to_agent">;
const NO_FLAGS: Flags = { secret: false, expose_to_shell: false, expose_to_agent: false };
const FLAG_LABELS: [keyof Flags, string][] = [
  ["secret", "Secret"],
  ["expose_to_shell", "Available to shell commands"],
  ["expose_to_agent", "Show to the agent"],
];

function FlagToggles({ flags, onChange, prefix }: { flags: Flags; onChange: (f: Flags) => void; prefix: string }) {
  return (
    <div className="flex flex-col gap-2">
      {FLAG_LABELS.map(([key, label]) => (
        <label key={key} className="flex items-center gap-2 text-ui">
          <input
            type="checkbox"
            className="size-4 accent-brand"
            data-testid={`${prefix}-${key}`}
            checked={Boolean(flags[key])}
            onChange={(e) => onChange({ ...flags, [key]: e.target.checked })}
          />
          {label}
        </label>
      ))}
    </div>
  );
}

function VarRow({ v, onApplied, onError }: { v: CustomVar } & Omit<Props, "vars">) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState("");
  const [flags, setFlags] = useState<Flags>({
    secret: v.secret,
    expose_to_shell: v.expose_to_shell,
    expose_to_agent: v.expose_to_agent,
  });
  const save = () =>
    api.envCustomUpdate({ name: v.name, ...flags, value: value === "" ? null : value }).then(
      (r) => {
        setEditing(false);
        setValue("");
        onApplied(r);
      },
      (err: unknown) => onError(errorText(err)),
    );
  return (
    <li className="grid gap-2 border-t py-2 first:border-t-0" data-testid={`custom-row-${v.name}`}>
      <div className="flex flex-wrap items-center gap-2">
        <code className="font-mono text-sm font-medium">{v.name}</code>
        <code className="font-mono text-xs text-muted-foreground">
          {v.secret ? (v.is_set ? "•••••• (set)" : "not set") : v.value}
        </code>
        {v.secret && <Badge variant="success">secret</Badge>}
        {v.expose_to_shell && <Badge variant="info">shell</Badge>}
        {v.expose_to_agent && <Badge variant="info">agent</Badge>}
        <span className="ml-auto flex gap-1">
          <Button size="icon-sm" variant="ghost" aria-label={`Edit ${v.name}`} onClick={() => setEditing((x) => !x)}>
            <PencilIcon aria-hidden />
          </Button>
          <Button
            size="icon-sm"
            variant="ghost"
            aria-label={`Delete ${v.name}`}
            data-testid={`custom-delete-${v.name}`}
            onClick={() => api.envCustomDelete(v.name).then(onApplied, (err: unknown) => onError(errorText(err)))}
          >
            <Trash2Icon aria-hidden />
          </Button>
        </span>
      </div>
      {editing && (
        <div className="grid gap-2 rounded-md bg-muted/40 p-2">
          <Input
            type={flags.secret ? "password" : "text"}
            aria-label={`New value for ${v.name}`}
            placeholder={flags.secret ? "new value (leave empty to keep)" : (v.value ?? "value")}
            value={value}
            onChange={(e) => setValue(e.target.value)}
          />
          <FlagToggles flags={flags} onChange={setFlags} prefix={`custom-edit-${v.name}`} />
          <div>
            <Button size="sm" onClick={() => void save()}>
              Save
            </Button>
          </div>
        </div>
      )}
    </li>
  );
}

export function CustomVarsCard({ vars, onApplied, onError }: Props) {
  const [name, setName] = useState("");
  const [value, setValue] = useState("");
  const [flags, setFlags] = useState<Flags>(NO_FLAGS);
  const [busy, setBusy] = useState(false);
  const add = async () => {
    setBusy(true);
    try {
      onApplied(await api.envCustomCreate({ name: name.trim(), value, ...flags }));
      setName("");
      setValue("");
      setFlags(NO_FLAGS);
    } catch (err) {
      onError(errorText(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card data-testid="custom-vars" className="gap-3">
      <CardHeader>
        <CardTitle className="text-sm">Add your own variable</CardTitle>
        <CardDescription>
          Reaches tools and shell commands from the next tool call. Secrets are encrypted and never shown again (and
          masked in tool output). Plain values also resolve as <code className="font-mono">{"{{var.NAME}}"}</code> in
          commands, jobs and workflows.
        </CardDescription>
      </CardHeader>
      <form
        className="grid gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          void add();
        }}
      >
        <div className="grid gap-2">
          <div className="grid gap-1">
            <Label htmlFor="custom-name">Name</Label>
            <Input
              id="custom-name"
              className="font-mono"
              placeholder="MY_VARIABLE"
              value={name}
              onChange={(e) => setName(e.target.value.toUpperCase())}
            />
          </div>
          <div className="grid gap-1">
            <Label htmlFor="custom-value">Value</Label>
            <Input
              id="custom-value"
              type={flags.secret ? "password" : "text"}
              autoComplete="off"
              placeholder="value"
              value={value}
              onChange={(e) => setValue(e.target.value)}
            />
          </div>
        </div>
        <FlagToggles flags={flags} onChange={setFlags} prefix="custom-new" />
        <div>
          <Button type="submit" variant="brand" className="w-full" disabled={busy || !name || !value} data-testid="custom-add">
            Add variable
          </Button>
        </div>
      </form>
      {vars.length > 0 && (
        <ul className="grid border-t pt-1" data-testid="custom-list">
          {vars.map((v) => (
            <VarRow key={v.name} v={v} onApplied={onApplied} onError={onError} />
          ))}
        </ul>
      )}
    </Card>
  );
}
