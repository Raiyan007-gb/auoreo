import type { QuestionAskedData } from "@sdk/contracts";
import { MessageCircleQuestionIcon } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useControl } from "@/lib/controlContext";
import { cn, errorText } from "@/lib/utils";

/** `question.asked` → options as toggle buttons, "Other" free text, multiSelect. */
export function QuestionForm({ request, onDone }: { request: QuestionAskedData; onDone?: () => void }) {
  const control = useControl();
  const [picked, setPicked] = useState<Record<number, string[]>>({});
  const [other, setOther] = useState<Record<number, string>>({});
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const toggle = (qi: number, label: string, multi: boolean) =>
    setPicked((p) => {
      const cur = p[qi] ?? [];
      const next = cur.includes(label) ? cur.filter((l) => l !== label) : multi ? [...cur, label] : [label];
      return { ...p, [qi]: next };
    });

  const submit = async () => {
    setBusy(true);
    setErr(null);
    const responses = request.questions.map((q, qi) => {
      const answers = [...(picked[qi] ?? [])];
      const free = other[qi]?.trim();
      if (free) answers.push(free);
      return { question: q.question, answers, answer: answers.join(", ") };
    });
    try {
      await control.answerQuestion(request.request_id, responses);
      onDone?.();
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  };

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
      {request.questions.map((q, qi) => (
        <fieldset key={qi} className="space-y-1.5">
          <legend className="flex items-center gap-1.5 text-sm font-medium">
            <MessageCircleQuestionIcon className="size-4 text-info" aria-hidden />
            {q.header && <span className="rounded bg-muted px-1 text-xs">{q.header}</span>}
            {q.question}
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
          <Input
            aria-label={`Other answer for: ${q.question}`}
            placeholder="Other…"
            className={cn("max-w-md")}
            value={other[qi] ?? ""}
            onChange={(e) => setOther((p) => ({ ...p, [qi]: e.target.value }))}
          />
        </fieldset>
      ))}
      <div className="flex items-center gap-2">
        <Button type="submit" size="sm" disabled={busy}>
          Send answer
        </Button>
        {err && (
          <span role="alert" className="text-xs text-destructive">
            {err}
          </span>
        )}
      </div>
    </form>
  );
}
