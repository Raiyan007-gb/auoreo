import type { ElicitationOpenData } from "@sdk/events";
import { ExternalLinkIcon, XIcon } from "lucide-react";
import { Button } from "@/components/ui/button";

/**
 * I5: `mcp.elicitation.open` — the user already consented in the question card; the link is
 * opened in a NEW tab only when they click "Open" here (a real user gesture: AUOREO never opens
 * or prefetches it on its own). Only http(s) links reach this card (thread.ts filters).
 */
export function ElicitationCard({ data, onDone }: { data: ElicitationOpenData; onDone: () => void }) {
  const open = () => {
    window.open(data.url, "_blank", "noopener,noreferrer");
    onDone();
  };
  return (
    <div
      role="group"
      aria-label={`Link from MCP server ${data.server}`}
      data-testid="elicitation-card"
      className="flex flex-wrap items-center gap-2 rounded-lg border border-info/50 bg-info/5 p-3 text-sm"
    >
      <ExternalLinkIcon className="size-4 shrink-0 text-info" aria-hidden />
      <div className="min-w-0 flex-1">
        <p>
          MCP server <span className="font-semibold">{data.server}</span> asks you to open a page on{" "}
          <span className="font-mono font-semibold" data-testid="elicitation-domain">
            {data.domain}
          </span>
        </p>
        <p className="truncate font-mono text-xs text-muted-foreground" title={data.url}>
          {data.url}
        </p>
      </div>
      <Button size="sm" onClick={open} data-testid="elicitation-open">
        <ExternalLinkIcon aria-hidden /> Open in new tab
      </Button>
      <Button size="sm" variant="ghost" aria-label="Dismiss link" onClick={onDone}>
        <XIcon />
      </Button>
    </div>
  );
}
