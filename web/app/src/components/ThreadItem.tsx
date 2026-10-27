import { CheckIcon, CodeIcon, CopyIcon, FileTextIcon } from "lucide-react";
import { useState } from "react";
import { MarkdownText } from "@/components/MarkdownLazy";
import { ThinkingBlock } from "@/components/ThinkingBlock";
import { ToolCard } from "@/components/ToolCard";
import { Button } from "@/components/ui/button";
import type { Item } from "@/lib/thread";
import { cn } from "@/lib/utils";

/** Per-message copy (and, for assistant replies, raw/rendered toggle) — I5, spec §17. */
function MessageTools({ text, raw, onRaw }: { text: string; raw?: boolean; onRaw?: () => void }) {
  const [copied, setCopied] = useState(false);
  const copy = () =>
    void navigator.clipboard
      ?.writeText(text)
      .then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      })
      .catch(() => undefined);
  return (
    <div className="flex gap-0.5 opacity-60 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
      <Button variant="ghost" size="icon-sm" aria-label={copied ? "Copied" : "Copy message"} data-testid="msg-copy" onClick={copy}>
        {copied ? <CheckIcon /> : <CopyIcon />}
      </Button>
      {onRaw && (
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={raw ? "Show rendered" : "Show raw Markdown"}
          aria-pressed={raw}
          data-testid="msg-raw-toggle"
          onClick={onRaw}
        >
          {raw ? <FileTextIcon /> : <CodeIcon />}
        </Button>
      )}
    </div>
  );
}

function AssistantMessage({ item, readOnly }: { item: Extract<Item, { kind: "assistant" }>; readOnly?: boolean }) {
  const [raw, setRaw] = useState(false);
  return (
    <div className="group flex max-w-[90%] items-start gap-1" data-testid="msg-assistant" data-streaming={item.streaming}>
      <div className="min-w-0 flex-1 leading-relaxed">
        {item.streaming ? (
          <div className="whitespace-pre-wrap">
            {item.text}
            <span aria-hidden className="ml-0.5 inline-block h-4 w-1.5 animate-pulse bg-foreground align-text-bottom" />
          </div>
        ) : raw ? (
          <pre className="rounded-lg bg-muted p-2.5 font-mono text-xs whitespace-pre-wrap" data-testid="msg-raw">
            {item.text}
          </pre>
        ) : (
          <MarkdownText text={item.text} />
        )}
      </div>
      {!item.streaming && !readOnly && <MessageTools text={item.text} raw={raw} onRaw={() => setRaw((r) => !r)} />}
    </div>
  );
}

/** One thread row (Session page, share page, Jobs/Teams run transcripts). */
export function ThreadItem({ item, awaiting = false, readOnly = false }: { item: Item; awaiting?: boolean; readOnly?: boolean }) {
  switch (item.kind) {
    case "user":
      return (
        <div className="group flex items-start justify-end gap-1" data-testid="msg-user">
          {!readOnly && <MessageTools text={item.text} />}
          <div className="max-w-[80%] rounded-[16px_16px_4px_16px] bg-primary px-3.5 py-2.5 whitespace-pre-wrap text-primary-foreground">
            {item.steer && <span className="mr-1 text-xs opacity-70">steer ·</span>}
            {item.text}
          </div>
        </div>
      );
    case "assistant":
      return <AssistantMessage item={item} readOnly={readOnly} />;
    case "reasoning":
      return <ThinkingBlock text={item.text} streaming={item.streaming} />;
    case "tool":
      return <ToolCard tool={item} awaitingApproval={awaiting} />;
    case "notice":
      return (
        <div
          role="status"
          data-testid="thread-notice"
          data-code={item.code}
          className={cn(
            "rounded-lg px-3 py-1.5 text-xs",
            item.level === "error" && "bg-destructive-soft text-destructive-ink",
            item.level === "warn" && "bg-warning-soft text-warning-ink",
            item.level === "info" && "bg-muted text-muted-foreground",
          )}
        >
          {item.text}
        </div>
      );
  }
}
