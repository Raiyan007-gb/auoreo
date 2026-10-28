import type { KanbanStatus, KanbanTask } from "@sdk/client";
import { PlusIcon } from "lucide-react";
import { type FormEvent, useState } from "react";
import { ErrorNote } from "@/components/AppShell";
import { AsyncBody, RefreshButton, SessionLink } from "@/components/ops/AsyncBody";
import { useAction } from "@/components/ops/opsUtils";
import { Badge, type BadgeVariant } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogTitle } from "@/components/ui/dialog";
import { Input, Label, Textarea } from "@/components/ui/input";
import { useAsync } from "@/hooks/useAsync";
import { api } from "@/lib/api";
import { relTime, truncate } from "@/lib/utils";

const COLUMNS: readonly { status: KanbanStatus; label: string; variant: BadgeVariant }[] = [
  { status: "ready", label: "Ready", variant: "secondary" },
  { status: "claimed", label: "Claimed", variant: "info" },
  { status: "blocked", label: "Blocked", variant: "warning" },
  { status: "done", label: "Done", variant: "success" },
];

function CreateTaskDialog({
  open,
  board,
  onOpenChange,
  onCreated,
}: {
  open: boolean;
  board: string;
  onOpenChange: (o: boolean) => void;
  onCreated: (t: KanbanTask) => void;
}) {
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [toBoard, setToBoard] = useState(board || "default");
  const action = useAction();
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const t = await action.run("create", () =>
      api.createKanban({ title: title.trim(), body: body.trim(), board: toBoard.trim() || "default" }),
    );
    if (t) {
      setTitle("");
      setBody("");
      onCreated(t);
      onOpenChange(false);
    }
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogTitle>New task</DialogTitle>
        <DialogDescription>Tasks start in “ready”; a team worker claims them with kanban_claim.</DialogDescription>
        <form onSubmit={(e) => void submit(e)} className="grid gap-3">
          <div className="grid gap-1">
            <Label htmlFor="task-title">Title</Label>
            <Input id="task-title" required value={title} onChange={(e) => setTitle(e.target.value)} autoFocus />
          </div>
          <div className="grid gap-1">
            <Label htmlFor="task-body">Details</Label>
            <Textarea id="task-body" rows={4} value={body} onChange={(e) => setBody(e.target.value)} />
          </div>
          <div className="grid gap-1">
            <Label htmlFor="task-board">Board</Label>
            <Input id="task-board" value={toBoard} onChange={(e) => setToBoard(e.target.value)} />
          </div>
          <ErrorNote error={action.error} />
          <DialogFooter>
            <Button variant="brand" type="submit" disabled={!title.trim() || action.busy !== null}>
              Create task
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function TaskCard({ t }: { t: KanbanTask }) {
  return (
    <li
      data-testid="kanban-card"
      data-id={t.id}
      className="space-y-1 rounded-md border bg-card p-2 text-card-foreground shadow-xs"
      aria-label={`Task ${t.id}: ${t.title}`}
    >
      <div className="flex items-start gap-2">
        <p className="min-w-0 flex-1 text-sm font-medium break-words">{t.title}</p>
        <span className="font-mono text-2xs text-muted-foreground">#{t.id}</span>
      </div>
      {t.body && <p className="text-xs whitespace-pre-wrap text-muted-foreground">{truncate(t.body, 240)}</p>}
      <div className="flex flex-wrap items-center gap-1 text-2xs text-muted-foreground">
        <Badge variant="outline">{t.board}</Badge>
        {t.worker && <span>worker {t.worker}</span>}
        {t.failures > 0 && <Badge variant="destructive">{t.failures} failures</Badge>}
        <span className="ml-auto" title={t.updated_at}>
          {relTime(t.updated_at)}
        </span>
      </div>
      {t.session_id && <SessionLink id={t.session_id} />}
    </li>
  );
}

export function KanbanBoard() {
  const [board, setBoard] = useState("");
  const tasks = useAsync(() => api.kanban(board.trim() || undefined), [board]);
  const [creating, setCreating] = useState(false);
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end gap-2">
        <div className="grid gap-1">
          <Label htmlFor="kanban-board">Board</Label>
          <Input
            id="kanban-board"
            placeholder="all boards"
            className="w-44"
            value={board}
            onChange={(e) => setBoard(e.target.value)}
          />
        </div>
        <div className="ml-auto flex items-center gap-1">
          <RefreshButton onClick={tasks.reload} busy={tasks.loading} label="Refresh board" />
          <Button variant="brand" onClick={() => setCreating(true)}>
            <PlusIcon aria-hidden /> New task
          </Button>
        </div>
      </div>
      <AsyncBody state={tasks} empty="" isEmpty={() => false}>
        {(rows) => (
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            {COLUMNS.map((c) => {
              const items = rows.filter((t) => t.status === c.status);
              return (
                <section
                  key={c.status}
                  data-testid="kanban-column"
                  data-status={c.status}
                  aria-labelledby={`col-${c.status}`}
                  className="flex min-h-40 flex-col gap-2 rounded-lg border bg-muted/30 p-2"
                >
                  <h2 id={`col-${c.status}`} className="flex items-center gap-2 px-1 text-sm font-medium">
                    {c.label}
                    <Badge variant={c.variant}>{items.length}</Badge>
                  </h2>
                  {items.length === 0 ? (
                    <p className="px-1 text-xs text-muted-foreground">No tasks.</p>
                  ) : (
                    <ul className="flex flex-col gap-2">
                      {items.map((t) => (
                        <TaskCard key={t.id} t={t} />
                      ))}
                    </ul>
                  )}
                </section>
              );
            })}
          </div>
        )}
      </AsyncBody>
      <CreateTaskDialog
        open={creating}
        board={board.trim()}
        onOpenChange={setCreating}
        onCreated={(t) => tasks.setData((l) => [...(l ?? []).filter((x) => x.id !== t.id), t])}
      />
    </div>
  );
}
