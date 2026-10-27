import type { GoalInfo, GoalStatus, SuggestionInfo } from "@sdk/client";
import { CheckIcon, MergeIcon, PlusIcon, XIcon } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useAsync } from "@/hooks/useAsync";
import { EmptyState, ErrorNote, PageHeader } from "@/components/AppShell";
import { Badge, type BadgeVariant } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input, Label, NativeSelect } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api } from "@/lib/api";
import { useControl } from "@/lib/controlContext";
import { errorText } from "@/lib/utils";

const SETTABLE = ["active", "paused", "done", "dropped"] as const;
const FILTERS = [
  "all",
  "active",
  "paused",
  "done",
  "dropped",
  "absorbed",
] as const;
type Filter = (typeof FILTERS)[number];

const GOAL_BADGE: Record<GoalStatus, BadgeVariant> = {
  active: "info",
  paused: "warning",
  done: "success",
  dropped: "outline",
  absorbed: "secondary",
};
const SUGGESTION_BADGE: Record<SuggestionInfo["status"], BadgeVariant> = {
  pending: "info",
  accepted: "success",
  denied: "outline",
  expired: "secondary",
  tombstoned: "destructive",
};

function ProgressBar({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  return (
    <div
      role="progressbar"
      aria-label="Progress"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={pct}
      className="h-2 w-full overflow-hidden rounded-full bg-muted"
    >
      <div
        className="h-full bg-primary transition-[width]"
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}

function AbsorbDialog({
  goal,
  goals,
  onClose,
}: {
  goal: GoalInfo | null;
  goals: GoalInfo[];
  onClose: () => void;
}) {
  const targets = goals.filter(
    (g) => goal && g.id !== goal.id && g.status !== "absorbed",
  );
  const [into, setInto] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const submit = async () => {
    if (!goal || !into) return;
    try {
      await api.absorbGoal(goal.id, Number(into));
      onClose();
    } catch (e) {
      setErr(errorText(e));
    }
  };
  return (
    <Dialog open={goal !== null} onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogTitle>Absorb goal</DialogTitle>
        <DialogDescription>
          “{goal?.title}” becomes absorbed; its evidence sessions merge into the
          goal you pick.
        </DialogDescription>
        <Label htmlFor="absorb-into">Absorb into</Label>
        <NativeSelect
          id="absorb-into"
          value={into}
          onChange={(e) => setInto(e.target.value)}
        >
          <option value="">Choose a goal…</option>
          {targets.map((g) => (
            <option key={g.id} value={g.id}>
              #{g.id} {g.title}
            </option>
          ))}
        </NativeSelect>
        {err && <ErrorNote error={err} />}
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="ghost">Cancel</Button>
          </DialogClose>
          <Button onClick={() => void submit()} disabled={!into}>
            <MergeIcon aria-hidden /> Absorb
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

interface Handlers {
  onError: (e: unknown) => void;
  onDone: () => void;
}

function GoalRow({
  goal,
  onAbsorb,
  onError,
  onDone,
}: { goal: GoalInfo; onAbsorb: () => void } & Handlers) {
  const [pct, setPct] = useState(String(Math.round(goal.progress * 100)));
  const absorbed = goal.status === "absorbed";
  const evidence = goal.evidence_session_ids ?? [];
  const save = (body: Parameters<typeof api.updateGoal>[1]) =>
    api.updateGoal(goal.id, body).then(onDone, onError);
  return (
    <li
      className="space-y-2 rounded-lg border p-3"
      data-testid="goal-row"
      data-goal-id={goal.id}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-xs text-muted-foreground">
          #{goal.id}
        </span>
        <span className="min-w-0 flex-1 truncate font-medium">
          {goal.title}
        </span>
        <Badge variant={GOAL_BADGE[goal.status]} data-testid="goal-status">
          {goal.status}
          {goal.absorbed_into ? ` → #${goal.absorbed_into}` : ""}
        </Badge>
      </div>
      {goal.description && (
        <p className="text-sm whitespace-pre-wrap text-muted-foreground">
          {goal.description}
        </p>
      )}
      <div className="flex items-center gap-2">
        <ProgressBar value={goal.progress} />
        <span
          className="w-10 text-right font-mono text-xs"
          data-testid="goal-progress"
        >
          {Math.round(goal.progress * 100)}%
        </span>
      </div>
      {!absorbed && (
        <form
          className="flex flex-wrap items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            void save({
              progress: Math.min(100, Math.max(0, Number(pct) || 0)) / 100,
            });
          }}
        >
          <Label htmlFor={`goal-pct-${goal.id}`} className="text-xs">
            Progress %
          </Label>
          <Input
            id={`goal-pct-${goal.id}`}
            type="number"
            min={0}
            max={100}
            className="h-7 w-20"
            value={pct}
            onChange={(e) => setPct(e.target.value)}
          />
          <Button size="sm" variant="outline" type="submit">
            Save
          </Button>
          <NativeSelect
            aria-label="Status"
            className="h-7 w-28"
            value={goal.status}
            onChange={(e) =>
              void save({ status: e.target.value as (typeof SETTABLE)[number] })
            }
          >
            {SETTABLE.map((s) => (
              <option key={s}>{s}</option>
            ))}
          </NativeSelect>
          <Button size="sm" variant="ghost" type="button" onClick={onAbsorb}>
            <MergeIcon aria-hidden /> Absorb…
          </Button>
          <span className="ml-auto text-xs text-muted-foreground">
            {evidence.length} evidence session{evidence.length === 1 ? "" : "s"}
          </span>
        </form>
      )}
    </li>
  );
}

function SuggestionCard({
  s,
  onError,
  onDone,
}: { s: SuggestionInfo } & Handlers) {
  const prompt = s.action?.type === "prompt" ? String(s.action.text ?? "") : "";
  return (
    <li
      className="space-y-2 rounded-lg border p-3"
      data-testid="suggestion-card"
      data-suggestion-id={s.id}
      data-status={s.status}
    >
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="outline">{s.kind}</Badge>
        <span className="min-w-0 flex-1 truncate font-medium">{s.title}</span>
        <Badge
          variant={SUGGESTION_BADGE[s.status]}
          data-testid="suggestion-status"
        >
          {s.status}
        </Badge>
      </div>
      {s.body && <p className="text-sm whitespace-pre-wrap">{s.body}</p>}
      {prompt && (
        <p className="text-xs text-muted-foreground">
          On accept runs: <span className="font-mono">{prompt}</span>
        </p>
      )}
      {s.status === "pending" && (
        <div className="flex gap-2">
          <Button
            size="sm"
            onClick={() =>
              void api.acceptSuggestion(s.id).then(onDone, onError)
            }
          >
            <CheckIcon aria-hidden /> Accept
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() => void api.denySuggestion(s.id).then(onDone, onError)}
          >
            <XIcon aria-hidden /> Deny
          </Button>
          <span className="ml-auto self-center text-xs text-muted-foreground">
            deliver: {s.deliver}
          </span>
        </div>
      )}
    </li>
  );
}

export function GoalsPage() {
  const control = useControl();
  const [filter, setFilter] = useState<Filter>("all");
  const goalsQ = useAsync(() => api.listGoals(filter), [filter]);
  const suggestionsQ = useAsync(() => api.listSuggestions("all", 50), []);
  const goals = goalsQ.data ?? null;
  const suggestions = suggestionsQ.data ?? null;
  const [title, setTitle] = useState("");
  const [absorbing, setAbsorbing] = useState<GoalInfo | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const onError = useCallback((e: unknown) => setErr(errorText(e)), []);

  const reloadGoals = goalsQ.reload;
  const reloadSuggestions = suggestionsQ.reload;
  const reload = useCallback(() => {
    reloadGoals();
    reloadSuggestions();
  }, [reloadGoals, reloadSuggestions]);
  useEffect(
    () =>
      control.listen((e) => {
        if (e.type.startsWith("goal.") || e.type.startsWith("suggestion."))
          reload();
      }),
    [control, reload],
  );

  const create = async () => {
    if (!title.trim()) return;
    try {
      await api.createGoal({ title: title.trim() });
      setTitle("");
      reload();
    } catch (e) {
      onError(e);
    }
  };
  const pending = (suggestions ?? []).filter((s) => s.status === "pending");
  const recent = (suggestions ?? [])
    .filter((s) => s.status !== "pending")
    .slice(0, 20);

  return (
    <div className="flex flex-col">
      <PageHeader
        title="Goals"
        description="Long-running goals and consent-first suggestions from your agents."
      />
      <div className="space-y-3 px-4 pt-4 pb-10 sm:px-8 sm:pt-5">
        {Boolean(err || goalsQ.error || suggestionsQ.error) && (
          <ErrorNote error={err ?? goalsQ.error ?? suggestionsQ.error} />
        )}
        <Tabs defaultValue="goals">
          <TabsList>
            <TabsTrigger value="goals">Goals</TabsTrigger>
            <TabsTrigger value="suggestions">
              Suggestions{pending.length ? ` (${pending.length})` : ""}
            </TabsTrigger>
          </TabsList>
          <TabsContent value="goals" className="space-y-3">
            <form
              className="flex flex-wrap gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                void create();
              }}
            >
              <Input
                aria-label="New goal"
                placeholder="New goal…"
                className="max-w-md"
                value={title}
                maxLength={300}
                onChange={(e) => setTitle(e.target.value)}
              />
              <Button type="submit" disabled={!title.trim()}>
                <PlusIcon aria-hidden /> Add goal
              </Button>
              <NativeSelect
                aria-label="Filter goals"
                className="ml-auto w-32"
                value={filter}
                onChange={(e) => setFilter(e.target.value as Filter)}
              >
                {FILTERS.map((f) => (
                  <option key={f}>{f}</option>
                ))}
              </NativeSelect>
            </form>
            {goals && goals.length === 0 && (
              <EmptyState title="No goals yet">
                Add one above or type /goal in a session.
              </EmptyState>
            )}
            <ul className="space-y-2" data-testid="goals-list">
              {(goals ?? []).map((g) => (
                <GoalRow
                  key={`${g.id}:${g.progress}`}
                  goal={g}
                  onAbsorb={() => setAbsorbing(g)}
                  onError={onError}
                  onDone={reload}
                />
              ))}
            </ul>
          </TabsContent>
          <TabsContent value="suggestions" className="space-y-3">
            {suggestions && pending.length === 0 && (
              <EmptyState title="No pending suggestions" />
            )}
            <ul className="space-y-2" data-testid="suggestions-pending">
              {pending.map((s) => (
                <SuggestionCard
                  key={s.id}
                  s={s}
                  onError={onError}
                  onDone={reload}
                />
              ))}
            </ul>
            {recent.length > 0 && (
              <h2 className="pt-2 text-sm font-medium text-muted-foreground">
                Recent
              </h2>
            )}
            <ul className="space-y-2" data-testid="suggestions-recent">
              {recent.map((s) => (
                <SuggestionCard
                  key={s.id}
                  s={s}
                  onError={onError}
                  onDone={reload}
                />
              ))}
            </ul>
          </TabsContent>
        </Tabs>
      </div>
      <AbsorbDialog
        key={absorbing?.id ?? 0}
        goal={absorbing}
        goals={goals ?? []}
        onClose={() => {
          setAbsorbing(null);
          reload();
        }}
      />
    </div>
  );
}
