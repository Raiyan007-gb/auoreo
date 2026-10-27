import { BrainIcon, ChevronRightIcon } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export function ThinkingToggle({ on, onChange }: { on: boolean; onChange: (v: boolean) => void }) {
  return (
    <Button
      size="sm"
      variant={on ? "secondary" : "outline"}
      aria-pressed={on}
      data-testid="thinking-toggle"
      title={on ? "Hide the model's thinking" : "Show the model's thinking"}
      onClick={() => onChange(!on)}
    >
      <BrainIcon aria-hidden /> Thinking {on ? "on" : "off"}
    </Button>
  );
}

/** Collapsible "Thinking" block for `message.reasoning` text (open while it streams). */
export function ThinkingBlock({ text, streaming }: { text: string; streaming: boolean }) {
  const [open, setOpen] = useState<boolean | null>(null);
  const expanded = open ?? streaming;
  return (
    <div className="max-w-[90%] rounded-lg border border-dashed bg-muted/40 text-xs" data-testid="thinking-block" data-streaming={streaming}>
      <button
        type="button"
        className="flex w-full items-center gap-1.5 px-2.5 py-1.5 text-left text-muted-foreground"
        aria-expanded={expanded}
        onClick={() => setOpen(!expanded)}
      >
        <ChevronRightIcon aria-hidden className={cn("size-3.5 transition-transform", expanded && "rotate-90")} />
        <BrainIcon aria-hidden className="size-3.5" />
        <span className="font-medium">{streaming ? "Thinking…" : "Thinking"}</span>
        {!expanded && <span className="truncate opacity-70">{text.slice(0, 80)}</span>}
      </button>
      {expanded && (
        <p className="px-3 pb-2 whitespace-pre-wrap text-muted-foreground" data-testid="thinking-text">
          {text}
        </p>
      )}
    </div>
  );
}
