import type { ExportFormat } from "@sdk/client";
import { ArchiveIcon, ArchiveRestoreIcon, FileDownIcon, FileJsonIcon, GitForkIcon, MinimizeIcon, Trash2Icon } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router";
import { ShareButton } from "@/components/ShareButton"; // --- I5
import { Button } from "@/components/ui/button";
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogTitle } from "@/components/ui/dialog";
import { Tip } from "@/components/ui/tooltip";
import { api } from "@/lib/api";
import { compactSession, type OpNotice } from "@/lib/sessionOps";
import { download, errorText } from "@/lib/utils";

/** Header actions for one session: fork, archive/unarchive, export, compact, delete. */
export function SessionActions({
  id,
  title,
  archived,
  running,
  onArchived,
  onNotice,
}: {
  id: string;
  title: string;
  archived: boolean;
  running: boolean;
  onArchived: (archived: boolean) => void;
  onNotice: (n: OpNotice) => void;
}) {
  const navigate = useNavigate();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [busy, setBusy] = useState(false);

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    try {
      await fn();
    } catch (e) {
      onNotice({ level: "error", text: errorText(e) });
    } finally {
      setBusy(false);
    }
  };

  const fork = () =>
    run(async () => {
      const f = await api.fork(id);
      void navigate(`/session/${f.id}`);
    });

  const toggleArchive = () =>
    run(async () => {
      const r = archived ? await api.unarchive(id) : await api.archive(id);
      onArchived(Boolean(r.archived));
      onNotice({ level: "info", text: r.archived ? "Session archived" : "Session restored from the archive" });
    });

  const exportAs = (format: ExportFormat) =>
    run(async () => {
      const res = await api.exportSession(id, format);
      download(`${id}.${format}`, await res.blob());
    });

  const compact = () => run(async () => onNotice(await compactSession(id)));

  const remove = () =>
    run(async () => {
      await api.deleteSession(id);
      void navigate("/sessions");
    });

  return (
    <div className="flex flex-wrap items-center gap-1" aria-label="Session actions">
      <ShareButton sessionId={id} />
      <Tip label="Fork into a new session with the same messages">
        <Button variant="outline" size="icon-sm" aria-label="Fork" onClick={() => void fork()} disabled={busy || running}>
          <GitForkIcon aria-hidden />
        </Button>
      </Tip>
      <Tip label="Compact: summarise older turns to free context">
        <Button variant="outline" size="icon-sm" aria-label="Compact" onClick={() => void compact()} disabled={busy || running}>
          <MinimizeIcon aria-hidden />
        </Button>
      </Tip>
      <Tip label="Download as Markdown">
        <Button variant="outline" size="icon-sm" onClick={() => void exportAs("md")} aria-label="Export as Markdown">
          <FileDownIcon aria-hidden />
        </Button>
      </Tip>
      <Tip label="Download as JSON">
        <Button variant="outline" size="icon-sm" onClick={() => void exportAs("json")} aria-label="Export as JSON">
          <FileJsonIcon aria-hidden />
        </Button>
      </Tip>
      <Tip label={archived ? "Unarchive" : "Archive"}>
        <Button variant="outline" size="icon-sm" aria-label={archived ? "Unarchive" : "Archive"} onClick={() => void toggleArchive()} disabled={busy}>
          {archived ? <ArchiveRestoreIcon aria-hidden /> : <ArchiveIcon aria-hidden />}
        </Button>
      </Tip>
      <Tip label="Delete session">
        <Button
          variant="destructive-outline"
          size="icon-sm"
          aria-label="Delete session"
          className="text-destructive hover:text-destructive"
          onClick={() => setConfirmDelete(true)}
          disabled={busy || running}
        >
          <Trash2Icon />
        </Button>
      </Tip>
      <Dialog open={confirmDelete} onOpenChange={setConfirmDelete}>
        <DialogContent>
          <DialogTitle>Delete session?</DialogTitle>
          <DialogDescription>
            “{title || "Untitled"}” and its messages are removed. The event log is kept.
          </DialogDescription>
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="ghost">Cancel</Button>
            </DialogClose>
            <Button
              variant="destructive"
              onClick={() => {
                setConfirmDelete(false);
                void remove();
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
