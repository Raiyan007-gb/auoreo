import type { QuestionAskedData } from "@sdk/events";
import { useState } from "react";
import { QuestionForm } from "@/components/QuestionForm";
import { cn, truncate } from "@/lib/utils";

function label(q: QuestionAskedData, i: number): string {
  const first = q.questions[0];
  return truncate(first?.header || first?.question || `Question ${i + 1}`, 28);
}

/**
 * I5 (spec §17 "question tabs"): several pending questions show as tabs over ONE form, so
 * the composer stays visible; a single question renders as before. Answering a tab removes
 * it and selects the next one.
 */
export function QuestionTabs({ pending, onDone }: { pending: QuestionAskedData[]; onDone: (id: string) => void }) {
  const [active, setActive] = useState<string | null>(null);
  if (pending.length === 0) return null;
  if (pending.length === 1) {
    const only = pending[0] as QuestionAskedData;
    return <QuestionForm key={only.request_id} request={only} onDone={() => onDone(only.request_id)} />;
  }
  const current = pending.find((q) => q.request_id === active) ?? (pending[0] as QuestionAskedData);
  return (
    <div className="space-y-1.5" data-testid="question-tabs">
      <div role="tablist" aria-label="Pending questions" className="flex flex-wrap gap-1">
        {pending.map((q, i) => {
          const on = q.request_id === current.request_id;
          return (
            <button
              key={q.request_id}
              type="button"
              role="tab"
              aria-selected={on}
              aria-controls={`qtab-${q.request_id}`}
              data-testid="question-tab"
              onClick={() => setActive(q.request_id)}
              className={cn(
                "rounded-md border px-2 py-0.5 text-xs",
                on ? "border-info bg-info/10 font-medium text-foreground" : "text-muted-foreground hover:bg-muted",
              )}
            >
              {i + 1}. {label(q, i)}
            </button>
          );
        })}
        <span className="self-center text-xs text-muted-foreground">{pending.length} waiting</span>
      </div>
      <div role="tabpanel" id={`qtab-${current.request_id}`}>
        <QuestionForm
          key={current.request_id}
          request={current}
          onDone={() => {
            setActive(null);
            onDone(current.request_id);
          }}
        />
      </div>
    </div>
  );
}
