import { ApiError, type HostCheck, type HostInfo, type HostWrite } from "@sdk/client";
import { PencilIcon, PlusIcon, ServerIcon, Trash2Icon, WifiIcon } from "lucide-react";
import { useState } from "react";
import { EmptyState, ErrorNote, PageHeader } from "@/components/AppShell";
import { AsyncBody, RefreshButton } from "@/components/ops/AsyncBody";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useAsync } from "@/hooks/useAsync";
import { api } from "@/lib/api";

/**
 * M7 SSH host inventory (`/api/hosts`, `settings.m7.ssh.web`). Fingerprints and state only:
 * keys are named, never shown or entered here, and trusting a host key, generating, deploying
 * or rotating a key are CLI commands (`auoreo ssh test|keygen|deploy|rotate`).
 */

const EMPTY: HostWrite = { host: "", port: 22, user: "", key_name: "", tags: [], jump: null, description: "" };

function toWrite(h: HostInfo): HostWrite {
  return {
    host: h.host,
    port: h.port,
    user: h.user,
    key_name: h.key_name,
    keepalive_interval_s: h.keepalive_interval_s,
    jump: h.jump ?? null,
    tags: h.tags,
    audit_commands: h.audit_commands,
    description: h.description,
  };
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1 text-xs font-medium">
      {label}
      {children}
    </label>
  );
}

function HostDialog({
  name,
  initial,
  onClose,
  onSaved,
}: {
  name: string | null;
  initial: HostWrite;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [draftName, setDraftName] = useState(name ?? "");
  const [form, setForm] = useState<HostWrite>(initial);
  const [tags, setTags] = useState((initial.tags ?? []).join(", "));
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const set = (patch: Partial<HostWrite>) => setForm((f) => ({ ...f, ...patch }));
  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      const list = tags.split(",").map((t) => t.trim()).filter(Boolean);
      await api.hostPut(draftName.trim(), { ...form, tags: list, jump: form.jump || null });
      onSaved();
      onClose();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-lg" data-testid="host-dialog">
        <DialogTitle>{name ? `Edit host ${name}` : "Add a host"}</DialogTitle>
        <DialogDescription>
          The key is the NAME of a stored secret, never key text. A new address or port is untrusted until you run{" "}
          <code>auoreo ssh test {draftName || "<name>"}</code> at the CLI.
        </DialogDescription>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Name">
            <Input value={draftName} disabled={name !== null} onChange={(e) => setDraftName(e.target.value)} data-testid="host-name" />
          </Field>
          <Field label="Key secret name">
            <Input value={form.key_name} onChange={(e) => set({ key_name: e.target.value })} data-testid="host-key" />
          </Field>
          <Field label="Host">
            <Input value={form.host} onChange={(e) => set({ host: e.target.value })} data-testid="host-host" />
          </Field>
          <Field label="Port">
            <Input type="number" value={form.port ?? 22} onChange={(e) => set({ port: Number(e.target.value) })} data-testid="host-port" />
          </Field>
          <Field label="User">
            <Input value={form.user} onChange={(e) => set({ user: e.target.value })} data-testid="host-user" />
          </Field>
          <Field label="Jump host (name)">
            <Input value={form.jump ?? ""} onChange={(e) => set({ jump: e.target.value })} data-testid="host-jump" />
          </Field>
          <Field label="Tags (comma separated)">
            <Input value={tags} onChange={(e) => setTags(e.target.value)} data-testid="host-tags" />
          </Field>
          <Field label="Description">
            <Input value={form.description ?? ""} onChange={(e) => set({ description: e.target.value })} />
          </Field>
        </div>
        <ErrorNote error={error} />
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={() => void save()} disabled={busy || !draftName.trim()} data-testid="host-save">
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function HostRow({
  h,
  onEdit,
  onDelete,
}: {
  h: HostInfo;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const [check, setCheck] = useState<HostCheck | null>(null);
  const [busy, setBusy] = useState(false);
  const run = async () => {
    setBusy(true);
    try {
      setCheck(await api.hostCheck(h.name));
    } catch (e) {
      setCheck({ ok: false, detail: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(false);
    }
  };
  return (
    <TableRow data-testid="host-row" data-name={h.name}>
      <TableCell>
        <p className="font-mono text-xs font-medium">{h.name}</p>
        {h.description && <p className="text-xs text-muted-foreground">{h.description}</p>}
      </TableCell>
      <TableCell className="font-mono text-xs">
        {h.user}@{h.host}:{h.port}
        {h.jump && <span className="block text-muted-foreground">via {h.jump}</span>}
      </TableCell>
      <TableCell>
        <code className="text-xs">{h.key_name}</code>
        <span className="block text-2xs text-muted-foreground" data-testid="host-key-state">
          {h.key_present ? h.key_fingerprint ?? "stored" : "missing: auoreo ssh keygen"}
        </span>
      </TableCell>
      <TableCell data-testid="host-trust">
        {h.trusted ? (
          <Badge variant="success" title={(h.host_key_fingerprints ?? []).join("\n")}>trusted</Badge>
        ) : (
          <span className="text-xs text-muted-foreground">
            untrusted: <code>auoreo ssh test {h.name}</code>
          </span>
        )}
      </TableCell>
      <TableCell>
        <div className="flex flex-wrap gap-1">
          {(h.tags ?? []).map((t) => (
            <Badge key={t} variant="outline">{t}</Badge>
          ))}
        </div>
      </TableCell>
      <TableCell>
        <Badge variant={h.usable_by_agents ? "success" : "outline"} title={h.problem ?? undefined}>
          {h.usable_by_agents ? "usable" : h.shadowed ? "shadowed" : "not usable"}
        </Badge>
        {check && (
          <p role="status" className={`text-2xs ${check.ok ? "text-success" : "text-destructive-ink"}`} data-testid="host-check">
            {check.detail}
          </p>
        )}
      </TableCell>
      <TableCell>
        <div className="flex gap-1">
          <Button size="sm" variant="outline" onClick={() => void run()} disabled={busy} aria-label={`Check ${h.name}`}>
            <WifiIcon aria-hidden /> Check
          </Button>
          <Button size="sm" variant="ghost" onClick={onEdit} aria-label={`Edit ${h.name}`}>
            <PencilIcon aria-hidden />
          </Button>
          <Button size="sm" variant="ghost" onClick={onDelete} aria-label={`Delete ${h.name}`}>
            <Trash2Icon aria-hidden />
          </Button>
        </div>
      </TableCell>
    </TableRow>
  );
}

export function HostsPage() {
  const list = useAsync(() => api.hosts(), []);
  const [editing, setEditing] = useState<{ name: string | null; initial: HostWrite } | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const off = list.error instanceof ApiError && list.error.status === 404;
  const remove = async (name: string) => {
    setError(null);
    try {
      await api.hostDelete(name);
      list.reload();
    } catch (e) {
      setError(e);
    } finally {
      setDeleting(null);
    }
  };
  return (
    <div className="flex flex-col">
      <PageHeader
        title="Hosts"
        description={
          <>
            SSH hosts agents may use (<code>hosts/&lt;name&gt;.yaml</code>). Keys and host-key trust are managed with{" "}
            <code>auoreo ssh</code> at the CLI; this page never shows key material.
          </>
        }
        actions={
          !off && (
            <Button size="sm" onClick={() => setEditing({ name: null, initial: EMPTY })} data-testid="host-add">
              <PlusIcon aria-hidden /> Add host
            </Button>
          )
        }
      />
      <div className="page-body space-y-2">
        {off ? (
          <EmptyState title="The hosts page is off" >
            <p data-testid="hosts-off">
              Turn it on with <code>auoreo config set m7.ssh.web true</code>; agents use hosts only with{" "}
              <code>m7.ssh.hosts</code>.
            </p>
          </EmptyState>
        ) : (
          <>
            <div className="flex items-center gap-2">
              <p className="flex-1 text-xs text-muted-foreground">
                {list.data && !list.data.enabled && (
                  <span data-testid="hosts-agents-off">Agents cannot use these hosts until m7.ssh.hosts is on. </span>
                )}
                Edits over the public tunnel are refused.
              </p>
              <RefreshButton onClick={list.reload} busy={list.loading} label="Refresh hosts" />
            </div>
            <ErrorNote error={error} />
            {(list.data?.problems ?? []).map((p) => (
              <p key={p} className="text-xs text-warning-ink">{p}</p>
            ))}
            <AsyncBody state={list} empty="No hosts" emptyHint="Add one here or with `auoreo ssh add`." isEmpty={(d) => d.hosts.length === 0}>
              {(data) => (
                <Table aria-label="SSH hosts">
                  <TableHeader>
                    <TableRow>
                      <TableHead>Name</TableHead>
                      <TableHead>Address</TableHead>
                      <TableHead>Key</TableHead>
                      <TableHead>Host key</TableHead>
                      <TableHead>Tags</TableHead>
                      <TableHead>State</TableHead>
                      <TableHead>
                        <span className="sr-only">Actions</span>
                      </TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.hosts.map((h) => (
                      <HostRow
                        key={h.name}
                        h={h}
                        onEdit={() => setEditing({ name: h.name, initial: toWrite(h) })}
                        onDelete={() => setDeleting(h.name)}
                      />
                    ))}
                  </TableBody>
                </Table>
              )}
            </AsyncBody>
          </>
        )}
      </div>
      {editing && <HostDialog name={editing.name} initial={editing.initial} onClose={() => setEditing(null)} onSaved={list.reload} />}
      <Dialog open={deleting !== null} onOpenChange={(o) => !o && setDeleting(null)}>
        <DialogContent data-testid="host-delete-dialog">
          <DialogTitle>
            <ServerIcon aria-hidden className="mr-1 inline size-4" /> Delete host {deleting}?
          </DialogTitle>
          <DialogDescription>Removes the host file only; the stored key and its known_hosts entry stay.</DialogDescription>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleting(null)}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={() => deleting && void remove(deleting)} data-testid="host-delete-confirm">
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
