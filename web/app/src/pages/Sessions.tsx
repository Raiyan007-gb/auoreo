import type { SessionInfo } from "@sdk/client";
import type { PlannedName } from "@sdk/contracts";
import { ArchiveIcon, GitForkIcon, PlayIcon, PlusIcon, RefreshCwIcon, Trash2Icon } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router";
import { EmptyState, ErrorNote, PageHeader } from "@/components/AppShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogTitle } from "@/components/ui/dialog";
import { Input, Label, NativeSelect } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tip } from "@/components/ui/tooltip";
import { useAsync, useSupports } from "@/hooks/useAsync";
import { api } from "@/lib/api";
import { STATUS_VARIANT } from "@/lib/status";
import { errorText, relTime } from "@/lib/utils";


function NewSessionDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const navigate = useNavigate();
  const agents = useAsync(() => api.harnessKind("agents"), []);
  const workflows = useAsync(() => api.harnessKind("workflows"), []);
  const [agent, setAgent] = useState("");
  const [workflow, setWorkflow] = useState("");
  const [title, setTitle] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const create = async () => {
    setBusy(true);
    setErr(null);
    try {
      const s = await api.createSession({
        agent: agent || null,
        workflow: workflow || null,
        title: title || null,
      });
      onOpenChange(false);
      void navigate(`/session/${s.id}`);
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogTitle>New session</DialogTitle>
        <DialogDescription>Pick an agent, or a workflow that chains agents.</DialogDescription>
        <form
          className="grid gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            void create();
          }}
        >
          <div className="grid gap-1">
            <Label htmlFor="ns-agent">Agent</Label>
            <NativeSelect id="ns-agent" value={agent} onChange={(e) => setAgent(e.target.value)}>
              <option value="">default</option>
              {agents.data?.map((a) => (
                <option key={a.name} value={a.name}>
                  {a.name} — {a.description}
                </option>
              ))}
            </NativeSelect>
          </div>
          <div className="grid gap-1">
            <Label htmlFor="ns-workflow">Workflow</Label>
            <NativeSelect id="ns-workflow" value={workflow} onChange={(e) => setWorkflow(e.target.value)}>
              <option value="">chat (single agent)</option>
              {workflows.data?.map((w) => (
                <option key={w.name} value={w.name}>
                  {w.name} — {w.description}
                </option>
              ))}
            </NativeSelect>
          </div>
          <div className="grid gap-1">
            <Label htmlFor="ns-title">Title (optional)</Label>
            <Input id="ns-title" value={title} onChange={(e) => setTitle(e.target.value)} />
          </div>
          {err && <ErrorNote error={err} />}
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="ghost">Cancel</Button>
            </DialogClose>
            <Button type="submit" disabled={busy}>
              Create
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/** Row action backed by a planned endpoint: disabled with a tooltip until the server has it. */
function PlannedAction({
  name,
  label,
  icon,
  onClick,
  destructive,
}: {
  name: PlannedName;
  label: string;
  icon: React.ReactNode;
  onClick: () => void;
  destructive?: boolean;
}) {
  const ok = useSupports(name);
  const btn = (
    <Button
      variant="ghost"
      size="icon-sm"
      aria-label={label}
      disabled={ok !== true}
      onClick={onClick}
      className={destructive ? "text-destructive hover:text-destructive" : undefined}
    >
      {icon}
    </Button>
  );
  return (
    <Tip label={ok === false ? `${label}: available after server upgrade` : label} wrap={ok !== true}>
      {btn}
    </Tip>
  );
}

export function SessionsPage() {
  const navigate = useNavigate();
  const sessions = useAsync(() => api.listSessions(), []);
  const [creating, setCreating] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState<SessionInfo | null>(null);
  const [actionErr, setActionErr] = useState<string | null>(null);

  const act = async (fn: () => Promise<unknown>) => {
    setActionErr(null);
    try {
      await fn();
      sessions.reload();
    } catch (e) {
      setActionErr(errorText(e));
    }
  };

  return (
    <div className="flex flex-col">
      <PageHeader
        title="Sessions"
        description="Every conversation with an agent or workflow."
        actions={
          <>
            <Button variant="outline" size="icon" aria-label="Refresh sessions" onClick={sessions.reload}>
              <RefreshCwIcon />
            </Button>
            <Button onClick={() => setCreating(true)}>
              <PlusIcon aria-hidden /> New session
            </Button>
          </>
        }
      />
      <div className="space-y-3 p-4">
        <ErrorNote error={sessions.error ?? actionErr} />
        {sessions.data && sessions.data.length === 0 ? (
          <EmptyState title="No sessions yet">Start one with “New session”.</EmptyState>
        ) : (
          <Table aria-label="Sessions">
            <TableHeader>
              <TableRow>
                <TableHead>Title</TableHead>
                <TableHead>Agent</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Updated</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {(sessions.data ?? []).map((s) => (
                <TableRow key={s.id} data-testid="session-row" data-session-id={s.id}>
                  <TableCell className="max-w-md">
                    <button
                      type="button"
                      className="truncate text-left font-medium hover:underline"
                      onClick={() => void navigate(`/session/${s.id}`)}
                    >
                      {s.title || "Untitled"}
                    </button>
                    <div className="font-mono text-[11px] text-muted-foreground">{s.id}</div>
                  </TableCell>
                  <TableCell>
                    <span className="font-mono text-xs">{s.agent}</span>
                    {s.workflow && s.workflow !== "chat" && (
                      <Badge variant="outline" className="ml-1">
                        {s.workflow}
                      </Badge>
                    )}
                  </TableCell>
                  <TableCell>
                    <Badge variant={STATUS_VARIANT[s.status] ?? "secondary"}>{s.status}</Badge>
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-xs text-muted-foreground" title={s.updated_at}>
                    {relTime(s.updated_at)}
                  </TableCell>
                  <TableCell>
                    <div className="flex justify-end gap-0.5">
                      <Tip label="Continue">
                        <Button variant="ghost" size="icon-sm" aria-label="Continue" onClick={() => void navigate(`/session/${s.id}`)}>
                          <PlayIcon />
                        </Button>
                      </Tip>
                      <PlannedAction
                        name="fork"
                        label="Fork"
                        icon={<GitForkIcon />}
                        onClick={() =>
                          void act(async () => {
                            const f = await api.fork(s.id);
                            void navigate(`/session/${f.id}`);
                          })
                        }
                      />
                      <PlannedAction
                        name={s.status === "archived" ? "unarchive" : "archive"}
                        label={s.status === "archived" ? "Unarchive" : "Archive"}
                        icon={<ArchiveIcon />}
                        onClick={() => void act(() => (s.status === "archived" ? api.unarchive(s.id) : api.archive(s.id)))}
                      />
                      <PlannedAction
                        name="deleteSession"
                        label="Delete"
                        destructive
                        icon={<Trash2Icon />}
                        onClick={() => setConfirmDelete(s)}
                      />
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </div>
      <NewSessionDialog open={creating} onOpenChange={setCreating} />
      <Dialog open={confirmDelete !== null} onOpenChange={(o) => !o && setConfirmDelete(null)}>
        <DialogContent>
          <DialogTitle>Delete session?</DialogTitle>
          <DialogDescription>
            “{confirmDelete?.title || "Untitled"}” and its messages are removed. The event log is kept.
          </DialogDescription>
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="ghost">Cancel</Button>
            </DialogClose>
            <Button
              variant="destructive"
              onClick={() => {
                const s = confirmDelete;
                setConfirmDelete(null);
                if (s) void act(() => api.deleteSession(s.id));
              }}
            >
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
