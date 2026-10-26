import { ChevronRightIcon, CopyIcon, FileDiffIcon } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { type DiffKind, type FileDiff, unified } from "@/lib/diff";
import { cn } from "@/lib/utils";

const ROW: Record<DiffKind, string> = {
  add: "bg-success/15 text-success",
  del: "bg-destructive/10 text-destructive",
  ctx: "text-muted-foreground",
  hunk: "bg-info/10 text-info italic",
  meta: "text-muted-foreground font-semibold",
};
const SIGN: Record<DiffKind, string> = { add: "+", del: "-", ctx: " ", hunk: "", meta: "" };

/** Collapsible unified diff for a file-editing tool card (I5). */
export function DiffView({ diff, defaultOpen = true }: { diff: FileDiff; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className="border-t" data-testid="tool-diff" data-open={open} aria-label={`Diff of ${diff.path || "file"}`}>
      <div className="flex items-center gap-2 px-3 py-1">
        <button
          type="button"
          className="flex min-w-0 flex-1 items-center gap-1.5 text-left text-xs hover:underline"
          aria-expanded={open}
          onClick={() => setOpen((o) => !o)}
          data-testid="tool-diff-toggle"
        >
          <ChevronRightIcon className={cn("size-3.5 shrink-0 transition-transform", open && "rotate-90")} aria-hidden />
          <FileDiffIcon className="size-3.5 shrink-0" aria-hidden />
          <span className="truncate font-mono">{diff.path || "(no path)"}</span>
          <span className="shrink-0 font-mono text-success" data-testid="diff-added">
            +{diff.added}
          </span>
          <span className="shrink-0 font-mono text-destructive" data-testid="diff-removed">
            −{diff.removed}
          </span>
        </button>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label="Copy diff"
          onClick={() => void navigator.clipboard?.writeText(unified(diff)).catch(() => undefined)}
        >
          <CopyIcon />
        </Button>
      </div>
      {open && (
        <pre className="max-h-96 overflow-auto px-0 pb-2 font-mono text-xs leading-5" data-testid="diff-lines">
          {diff.lines.map((l, i) => (
            <div key={i} className={cn("px-3 whitespace-pre-wrap", ROW[l.kind])} data-kind={l.kind}>
              <span aria-hidden className="mr-2 inline-block w-3 select-none opacity-70">
                {SIGN[l.kind]}
              </span>
              {l.text || " "}
            </div>
          ))}
        </pre>
      )}
    </section>
  );
}
