import type { SessionPlan } from "@sdk/client";
import type { PlanStepData, PlanUpdatedData } from "@sdk/events";
import { CheckCircle2Icon, ChevronDownIcon, CircleDotIcon, CircleIcon, ListTodoIcon } from "lucide-react";
import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

const ICON = {
  completed: <CheckCircle2Icon aria-hidden className="size-3.5 text-success" />,
  in_progress: <CircleDotIcon aria-hidden className="size-3.5 text-info" />,
  pending: <CircleIcon aria-hidden className="size-3.5 text-muted-foreground" />,
} as const;

/**
 * The session's current `update_plan` todo list: live from `plan.updated` events (thread
 * state), seeded from `GET /api/sessions/{id}/plan` for sessions whose events predate the page.
 */
export function PlanPanel({
  sessionId,
  plan,
  planMode,
}: {
  sessionId: string;
  plan: PlanUpdatedData | undefined;
  planMode?: boolean;
}) {
  const [seed, setSeed] = useState<SessionPlan | null>(null);
  const [open, setOpen] = useState(true);
  useEffect(() => {
    let cancelled = false;
    api
      .sessionPlan(sessionId)
      .then((p) => !cancelled && setSeed(p))
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  const steps: readonly PlanStepData[] = plan?.steps ?? (seed?.steps as PlanStepData[] | undefined) ?? [];
  const inPlanMode = planMode ?? seed?.plan_mode ?? false;
  if (steps.length === 0 && !inPlanMode) return null;
  const done = steps.filter((s) => s.status === "completed").length;
  const explanation = plan?.explanation ?? seed?.explanation ?? "";

  return (
    <section className="rounded-lg border bg-card text-sm" aria-label="Plan" data-testid="plan-panel">
      <button
        type="button"
        className="flex w-full items-center gap-2 px-3 py-1.5 text-left"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        <ListTodoIcon aria-hidden className="size-4 text-info" />
        <span className="font-medium">Plan</span>
        <Badge variant="secondary" data-testid="plan-progress">
          {done}/{steps.length}
        </Badge>
        {inPlanMode && <Badge variant="info">plan mode</Badge>}
        <ChevronDownIcon aria-hidden className={cn("ml-auto size-4 transition-transform", !open && "-rotate-90")} />
      </button>
      {open && (
        <div className="space-y-1 px-3 pb-2">
          {explanation && <p className="text-xs text-muted-foreground">{explanation}</p>}
          <ol className="max-h-40 space-y-0.5 overflow-auto">
            {steps.map((s, i) => (
              <li key={i} className="flex items-start gap-1.5" data-testid="plan-step" data-status={s.status}>
                <span className="mt-0.5">{ICON[s.status] ?? ICON.pending}</span>
                <span className={cn(s.status === "completed" && "text-muted-foreground line-through")}>{s.step}</span>
                {s.done_when && <span className="text-xs text-muted-foreground">— done when: {s.done_when}</span>}
              </li>
            ))}
          </ol>
        </div>
      )}
    </section>
  );
}
