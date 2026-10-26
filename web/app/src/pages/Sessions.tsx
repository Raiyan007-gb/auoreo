import type { SessionInfo } from "@sdk/client";
import { ArchiveIcon, ArchiveRestoreIcon, GitForkIcon, ListPlusIcon, PlayIcon, PlusIcon, RefreshCwIcon, Trash2Icon } from "lucide-react";
import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { EmptyState, ErrorNote, PageHeader } from "@/components/AppShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogTitle } from "@/components/ui/dialog";
import { Input, Label, NativeSelect, Textarea } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tip } from "@/components/ui/tooltip";
import { WelcomeBanner } from "@/components/WelcomeBanner";
import { useAsync } from "@/hooks/useAsync";
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

function RowAction({
  label,
  icon,
  onClick,
  destructive,
}: {
  label: string;
  icon: React.ReactNode;
  onClick: () => void;
  destructive?: boolean;
}) {
  return (
    <Tip label={label}>
      <Button
        variant="ghost"
        size="icon-sm"
        aria-label={label}
        onClick={onClick}
        className={destructive ? "text-destructive hover:text-destructive" : undefined}
      >
        {icon}
      </Button>
    </Tip>
  );
}

/** I5: queue a follow-up turn on a session from the table (runs after its current turn). */
function QueueDialog({ session, onClose, onQueued }: { session: SessionInfo | null; onClose: () => void; onQueued: (msg: string) => void }) {
  const [text, setText] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    if (!session || !text.trim()) return;
    setBusy(true);
    setErr(null);
    try {
      const r = await api.chat({ session_id: session.id, message: text.trim(), mode: "followup" });
      setText("");
      onQueued(`Queued on “${session.title || session.id}”${r.run_id ? ` (run ${r.run_id})` : ""}`);
      onClose();
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog open={session !== null} onOpenChange={(o) => !o && onClose()}>
      <DialogContent data-testid="queue-dialog">
        <DialogTitle>Queue a message</DialogTitle>
        <DialogDescription>Runs on “{session?.title || session?.id}” after its current turn (or right away when idle).</DialogDescription>
        <form
          className="grid gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            void submit();
          }}
        >
          <Textarea aria-label="Message to queue" data-testid="queue-input" rows={3} value={text} onChange={(e) => setText(e.target.value)} />
          {err && <ErrorNote error={err} />}
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="ghost">Cancel</Button>
            </DialogClose>
            <Button type="submit" disabled={busy || !text.trim()} data-testid="queue-submit">
              Queue
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/** Last path segment of the session's working directory, full path in the tooltip. */
function ProjectCell({ path }: { path: string | null | undefined }) {
  if (!path) return <span className="text-xs text-muted-foreground">—</span>;
  const name = path.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || path;
  return (
    <span className="font-mono text-xs" title={path} data-testid="session-project">
      {name}
    </span>
  );
}

interface Listing {
  rows: SessionInfo[];
  /** Ids of archived sessions (only computed when the archived filter is on). */
  archived: ReadonlySet<string>;
}

/** `?archived=true` lists archived AND active sessions; diff against the default list to mark them. */
async function loadListing(showArchived: boolean): Promise<Listing> {
  if (!showArchived) return { rows: await api.listSessions(), archived: new Set() };
  const [all, active] = await Promise.all([api.listSessions(200, true), api.listSessions(1000)]);
  const activeIds = new Set(active.map((s) => s.id));
  return { rows: all, archived: new Set(all.filter((s) => !activeIds.has(s.id)).map((s) => s.id)) };
}

export function SessionsPage() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const showArchived = params.get("archived") === "true";
  const sessions = useAsync(() => loadListing(showArchived), [showArchived]);
  const rows = sessions.data?.rows;
  const isArchived = (id: string) => sessions.data?.archived.has(id) ?? false;
  const [creating, setCreating] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState<SessionInfo | null>(null);
  const [actionErr, setActionErr] = useState<string | null>(null);
  const [queueFor, setQueueFor] = useState<SessionInfo | null>(null); // --- I5
  const [queued, setQueued] = useState<string | null>(null);

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
            <Button
              variant={showArchived ? "secondary" : "outline"}
              size="sm"
              aria-pressed={showArchived}
              data-testid="archived-filter"
              onClick={() => setParams(showArchived ? {} : { archived: "true" })}
            >
              <ArchiveIcon aria-hidden /> {showArchived ? "Showing archived" : "Show archived"}
            </Button>
            <Button variant="outline" size="icon" aria-label="Refresh sessions" onClick={sessions.reload}>
              <RefreshCwIcon />
            </Button>
            <Button onClick={() => setCreating(true)}>
              <PlusIcon aria-hidden /> New session
            </Button>
          </>
        }
      />
      <WelcomeBanner />
      <div className="space-y-3 p-4">
        <ErrorNote error={sessions.error ?? actionErr} />
        {queued && (
          <p role="status" className="text-xs text-muted-foreground" data-testid="queue-notice">
            {queued}
          </p>
        )}
        {rows && rows.length === 0 ? (
          <EmptyState title="No sessions yet">Start one with “New session”.</EmptyState>
        ) : (
          <Table aria-label="Sessions">
            <TableHeader>
              <TableRow>
                <TableHead>Title</TableHead>
                <TableHead>Project</TableHead>
                <TableHead>Agent</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Updated</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {(rows ?? []).map((s) => (
                <TableRow key={s.id} data-testid="session-row" data-session-id={s.id} data-archived={isArchived(s.id)}>
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
                  <TableCell className="max-w-40 truncate">
                    <ProjectCell path={s.project} />
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
                    {isArchived(s.id) && (
                      <Badge variant="outline" className="ml-1">
                        archived
                      </Badge>
                    )}
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
                      <RowAction label="Queue" icon={<ListPlusIcon />} onClick={() => setQueueFor(s)} />
                      <RowAction
                        label="Fork"
                        icon={<GitForkIcon />}
                        onClick={() =>
                          void act(async () => {
                            const f = await api.fork(s.id);
                            void navigate(`/session/${f.id}`);
                          })
                        }
                      />
                      <RowAction
                        label={isArchived(s.id) ? "Unarchive" : "Archive"}
                        icon={isArchived(s.id) ? <ArchiveRestoreIcon /> : <ArchiveIcon />}
                        onClick={() => void act(() => (isArchived(s.id) ? api.unarchive(s.id) : api.archive(s.id)))}
                      />
                      <RowAction label="Delete" destructive icon={<Trash2Icon />} onClick={() => setConfirmDelete(s)} />
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </div>
      <NewSessionDialog open={creating} onOpenChange={setCreating} />
      <QueueDialog session={queueFor} onClose={() => setQueueFor(null)} onQueued={(m) => {
          setQueued(m);
          sessions.reload();
        }}
      />
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
