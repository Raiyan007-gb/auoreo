import { ChevronRightIcon, CopyIcon, FileTextIcon } from "lucide-react";
import { useMemo, useState } from "react";
import { DiffView } from "@/components/DiffView";
import { Button } from "@/components/ui/button";
import { toolDiff } from "@/lib/diff";
import type { ToolItem, ToolStatus } from "@/lib/thread";
import { argsPreview, prettyArgs } from "@/lib/toolPreview";
import { cn, truncate } from "@/lib/utils";

const DOT: Record<ToolStatus, string> = {
  pending: "bg-muted-foreground/50",
  running: "bg-info animate-pulse",
  completed: "bg-success",
  error: "bg-destructive",
};

export function StatusDot({ status, label }: { status: ToolStatus; label?: string }) {
  return (
    <span
      role="img"
      aria-label={label ?? status}
      data-status={status}
      className={cn("inline-block size-2 shrink-0 rounded-full", DOT[status])}
    />
  );
}

export function ToolCard({ tool, awaitingApproval }: { tool: ToolItem; awaitingApproval?: boolean }) {
  const [open, setOpen] = useState(false);
  const outputPath = typeof tool.meta?.output_path === "string" ? tool.meta.output_path : null;
  const statusLabel = awaitingApproval ? "awaiting approval" : tool.status;
  const resultLine = tool.result ? truncate(tool.result.replace(/\s+/g, " ").trim(), 120) : "";
  const panelId = `tool-${tool.key}`;
  const diff = useMemo(() => toolDiff(tool.name, tool.args), [tool.name, tool.args]); // --- I5

  return (
    <div
      className="rounded-lg border bg-card text-card-foreground"
      data-testid="tool-card"
      data-tool={tool.name}
      data-status={tool.status}
    >
      <button
        type="button"
        className="flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-muted/40"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((o) => !o)}
      >
        <ChevronRightIcon className={cn("size-3.5 shrink-0 transition-transform", open && "rotate-90")} aria-hidden />
        <StatusDot status={awaitingApproval ? "pending" : tool.status} label={statusLabel} />
        <span className="font-mono text-xs font-semibold">{tool.name}</span>
        <span className="min-w-0 flex-1 truncate font-mono text-xs text-muted-foreground">{argsPreview(tool.args)}</span>
        <span
          className={cn(
            "shrink-0 text-xs",
            tool.status === "error" ? "text-destructive" : awaitingApproval ? "text-warning" : "text-muted-foreground",
          )}
        >
          {statusLabel}
        </span>
      </button>
      {diff && <DiffView diff={diff} defaultOpen={tool.status !== "completed"} />}
      {!open && resultLine && (
        <div className="truncate border-t px-3 py-1 font-mono text-xs text-muted-foreground" data-testid="tool-result-preview">
          {resultLine}
        </div>
      )}
      {open && (
        <div id={panelId} className="space-y-2 border-t px-3 py-2">
          <section>
            <h4 className="mb-1 text-xs font-medium text-muted-foreground">Arguments</h4>
            <pre className="max-h-48 overflow-auto rounded-md bg-muted p-2 font-mono text-xs whitespace-pre-wrap">
              {prettyArgs(tool.args)}
            </pre>
          </section>
          {tool.result !== undefined && (
            <section>
              <h4 className="mb-1 text-xs font-medium text-muted-foreground">Result</h4>
              <pre
                className={cn(
                  "max-h-80 overflow-auto rounded-md bg-muted p-2 font-mono text-xs whitespace-pre-wrap",
                  tool.status === "error" && "text-destructive",
                )}
              >
                {tool.result}
              </pre>
            </section>
          )}
          {outputPath && (
            <div className="flex items-center gap-2 text-xs">
              <FileTextIcon className="size-3.5" aria-hidden />
              <span className="text-muted-foreground">Full output spilled to</span>
              <a className="truncate font-mono underline" href={`file://${outputPath}`} title={outputPath}>
                {outputPath}
              </a>
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label="Copy output path"
                onClick={() => void navigator.clipboard?.writeText(outputPath)}
              >
                <CopyIcon />
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
