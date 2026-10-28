import { ChevronRightIcon } from "lucide-react";
import { useState } from "react";
import { ToolCard } from "@/components/ToolCard";
import type { ToolGroupRow } from "@/lib/toolGroups";
import { summarizeTools } from "@/lib/toolGroups";
import { cn } from "@/lib/utils";

/** A finished run of tool calls as one row: "5 tool calls · read ×3 · grep ×2", open to see each card. */
export function ToolGroup({ group }: { group: ToolGroupRow }) {
  const [open, setOpen] = useState(false);
  const failed = group.tools.filter((t) => t.status === "error").length;
  const panel = `toolgroup-${group.key}`;
  return (
    <div className="rounded-lg border border-dashed bg-card/60" data-testid="tool-group" data-count={group.tools.length}>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panel}
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 px-3.5 py-2 text-left text-xs hover:bg-surface-hover"
      >
        <ChevronRightIcon className={cn("size-3.5 shrink-0 transition-transform", open && "rotate-90")} aria-hidden />
        <span className="font-semibold">{group.tools.length} tool calls</span>
        <span className="min-w-0 flex-1 truncate font-mono text-muted-foreground">{summarizeTools(group.tools)}</span>
        {failed > 0 && <span className="shrink-0 font-medium text-destructive">{failed} failed</span>}
      </button>
      {open && (
        <div id={panel} className="flex flex-col gap-2 border-t border-dashed p-2">
          {group.tools.map((t) => (
            <ToolCard key={t.key} tool={t} />
          ))}
        </div>
      )}
    </div>
  );
}
