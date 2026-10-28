import type { ExportFormat } from "@sdk/client";
import {
  ArchiveIcon,
  ArchiveRestoreIcon,
  EllipsisIcon,
  FileDownIcon,
  FileJsonIcon,
  GitForkIcon,
  MinimizeIcon,
  Trash2Icon,
} from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router";
import { ShareButton } from "@/components/ShareButton"; // --- I5
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogTitle } from "@/components/ui/dialog";
import { Tip } from "@/components/ui/tooltip";
import { api } from "@/lib/api";
import { compactSession, type OpNotice } from "@/lib/sessionOps";
import { download, errorText } from "@/lib/utils";

/** Header actions for one session: Share stays visible; fork, compact, export, archive and delete sit in a "More" menu. */
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
      {/* Not modal: a modal menu hides the rest of the page with aria-hidden while its controls stay focusable. */}
      <DropdownMenu modal={false}>
        <Tip label="More actions">
          <DropdownMenuTrigger asChild>
            <Button variant="outline" size="icon-sm" aria-label="More actions" data-testid="session-more">
              <EllipsisIcon aria-hidden />
            </Button>
          </DropdownMenuTrigger>
        </Tip>
        <DropdownMenuContent>
          <DropdownMenuItem disabled={busy || running} onSelect={() => void fork()}>
            <GitForkIcon aria-hidden /> Fork
          </DropdownMenuItem>
          <DropdownMenuItem disabled={busy || running} onSelect={() => void compact()}>
            <MinimizeIcon aria-hidden /> Compact
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem onSelect={() => void exportAs("md")}>
            <FileDownIcon aria-hidden /> Export as Markdown
          </DropdownMenuItem>
          <DropdownMenuItem onSelect={() => void exportAs("json")}>
            <FileJsonIcon aria-hidden /> Export as JSON
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem disabled={busy} onSelect={() => void toggleArchive()}>
            {archived ? <ArchiveRestoreIcon aria-hidden /> : <ArchiveIcon aria-hidden />}
            {archived ? "Unarchive" : "Archive"}
          </DropdownMenuItem>
          <DropdownMenuItem destructive disabled={busy || running} onSelect={() => setConfirmDelete(true)}>
            <Trash2Icon aria-hidden /> Delete session
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
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
