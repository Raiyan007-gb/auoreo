import type { QuestionAskedData, QuestionItem } from "@sdk/events";
import { CheckIcon, ClipboardListIcon, MessageCircleQuestionIcon, PencilIcon, TimerIcon } from "lucide-react";
import { useEffect, useState } from "react";
import { MarkdownText } from "@/components/MarkdownLazy";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useControl } from "@/lib/controlContext";
import { cn, errorText } from "@/lib/utils";

type Response = { question: string; answers: string[]; customText?: string };

/** Seconds left until `expires_at` (unix seconds), ticking once a second; null without a deadline. */
function useCountdown(expiresAt: number | undefined): number | null {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (!expiresAt) return;
    const t = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(t);
  }, [expiresAt]);
  return expiresAt ? Math.max(0, Math.round(expiresAt - now)) : null;
}

/** `exit_plan_mode`: the plan as Markdown, then Approve / Keep planning (+ optional feedback). */
function PlanReview({ q, busy, onAnswer }: { q: QuestionItem; busy: boolean; onAnswer: (r: Response[]) => void }) {
  const [feedback, setFeedback] = useState("");
  const answer = (label: string) =>
    onAnswer([{ question: q.question, answers: [label], ...(feedback.trim() ? { customText: feedback.trim() } : {}) }]);
  const [approve, keep] = [q.options?.[0]?.label ?? "Approve", q.options?.[1]?.label ?? "Keep planning"];
  return (
    <div className="space-y-2" data-testid="plan-review">
      <p className="flex items-center gap-1.5 text-sm font-medium">
        <ClipboardListIcon className="size-4 text-info" aria-hidden /> {q.question}
      </p>
      <div className="max-h-72 overflow-auto rounded-md border bg-background p-3 text-sm" data-testid="plan-markdown">
        <MarkdownText text={q.plan ?? ""} />
      </div>
      <Input
        aria-label="Feedback on the plan (optional)"
        placeholder="Feedback for the agent (optional)"
        className="max-w-xl"
        value={feedback}
        onChange={(e) => setFeedback(e.target.value)}
      />
      <div className="flex gap-2">
        <Button size="sm" disabled={busy} onClick={() => answer(approve)}>
          <CheckIcon aria-hidden /> Approve plan
        </Button>
        <Button size="sm" variant="outline" disabled={busy} onClick={() => answer(keep)}>
          <PencilIcon aria-hidden /> Keep planning
        </Button>
      </div>
    </div>
  );
}

/** `question.asked` → options as toggle buttons, optional free text (`allowCustom`), multiSelect. */
export function QuestionForm({ request, onDone }: { request: QuestionAskedData; onDone?: () => void }) {
  const control = useControl();
  const [picked, setPicked] = useState<Record<number, string[]>>({});
  const [other, setOther] = useState<Record<number, string>>({});
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const left = useCountdown(request.expires_at);
  const plan = request.questions.length === 1 && request.questions[0]?.plan !== undefined ? request.questions[0] : null;

  const toggle = (qi: number, label: string, multi: boolean) =>
    setPicked((p) => {
      const cur = p[qi] ?? [];
      const next = cur.includes(label) ? cur.filter((l) => l !== label) : multi ? [...cur, label] : [label];
      return { ...p, [qi]: next };
    });

  const send = async (responses: Response[]) => {
    setBusy(true);
    setErr(null);
    try {
      await control.answerQuestion(request.request_id, responses);
      onDone?.();
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const submit = () =>
    send(
      request.questions.map((q, qi) => {
        const free = q.allowCustom === false ? "" : (other[qi]?.trim() ?? "");
        return { question: q.question, answers: [...(picked[qi] ?? [])], ...(free ? { customText: free } : {}) };
      }),
    );

  return (
    <form
      className="space-y-3 rounded-lg border border-info/50 bg-info/5 p-3"
      aria-label="Agent question"
      data-testid="question-form"
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      {left !== null && (
        <p className={cn("flex items-center gap-1 text-xs text-muted-foreground", left <= 10 && "text-warning")} data-testid="question-timer">
          <TimerIcon aria-hidden className="size-3.5" /> {left > 0 ? `Agent waits ${left}s more` : "Timed out — the agent continues with a default"}
        </p>
      )}
      {plan ? (
        <PlanReview q={plan} busy={busy} onAnswer={(r) => void send(r)} />
      ) : (
        request.questions.map((q, qi) => (
          <fieldset key={qi} className="space-y-1.5" data-testid="question" data-allow-custom={q.allowCustom !== false}>
            <legend className="flex items-center gap-1.5 text-sm font-medium">
              <MessageCircleQuestionIcon className="size-4 text-info" aria-hidden />
              {q.header && <span className="rounded bg-muted px-1 text-xs">{q.header}</span>}
              {q.question}
              {q.multiSelect && <span className="text-xs font-normal text-muted-foreground">(pick any)</span>}
            </legend>
            <div className="flex flex-wrap gap-1.5">
              {(q.options ?? []).map((o) => {
                const on = (picked[qi] ?? []).includes(o.label);
                return (
                  <Button
                    key={o.label}
                    size="sm"
                    variant={on ? "default" : "outline"}
                    aria-pressed={on}
                    title={o.description}
                    onClick={() => toggle(qi, o.label, q.multiSelect === true)}
                  >
                    {o.label}
                  </Button>
                );
              })}
            </div>
            {q.allowCustom !== false && (
              <Input
                aria-label={`Other answer for: ${q.question}`}
                placeholder={(q.options ?? []).length ? "Other…" : "Your answer"}
                className="max-w-md"
                value={other[qi] ?? ""}
                onChange={(e) => setOther((p) => ({ ...p, [qi]: e.target.value }))}
              />
            )}
          </fieldset>
        ))
      )}
      <div className="flex items-center gap-2">
        {!plan && (
          <Button type="submit" size="sm" disabled={busy}>
            Send answer
          </Button>
        )}
        {err && (
          <span role="alert" className="text-xs text-destructive">
            {err}
          </span>
        )}
      </div>
    </form>
  );
}
