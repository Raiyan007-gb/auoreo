import type { PendingQuestion } from "@sdk/client";
import type { QuestionAskedData } from "@sdk/events";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { useControl } from "@/lib/controlContext";

/** Seed once the control channel is up (or known to be unavailable): fetching on mount too would
 *  ask twice per page load, since `connected` flips false → true right after. */
function useSeedReady(): boolean {
  const control = useControl();
  return Boolean(control.connected) || control.state === "unavailable";
}

/**
 * Questions the agents are waiting on (`ask_user_question`, plan reviews, MCP consent cards):
 * seeded from `GET /api/questions/pending`, then kept live by `question.asked` / `question.answered`.
 * Expired ones drop out on the next render.
 */
export function usePendingQuestions(): { items: PendingQuestion[]; done: (requestId: string) => void } {
  const control = useControl();
  const ready = useSeedReady();
  const [all, setAll] = useState<PendingQuestion[]>([]);

  useEffect(() => {
    if (!ready) return;
    let cancelled = false;
    api
      .pendingQuestions()
      .then((q) => !cancelled && setAll(q))
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [ready]);

  useEffect(
    () =>
      control.listen((e) => {
        if (e.type === "question.asked") {
          const d = e.data as unknown as QuestionAskedData;
          const row: PendingQuestion = {
            request_id: d.request_id,
            session_id: e.aggregate_id,
            questions: d.questions as unknown as PendingQuestion["questions"],
            timeout_s: d.timeout_s ?? null,
            expires_at: d.expires_at ?? null,
          };
          setAll((s) => (s.some((x) => x.request_id === row.request_id) ? s : [...s, row]));
        } else if (e.type === "question.answered") {
          const rid = String(e.data.request_id ?? "");
          setAll((s) => s.filter((x) => x.request_id !== rid));
        }
      }),
    [control],
  );

  const done = useCallback((rid: string) => setAll((s) => s.filter((x) => x.request_id !== rid)), []);
  // A clock for the expiry filter (a render must not read the time itself); coarse is enough.
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now() / 1000), 15_000);
    return () => clearInterval(t);
  }, []);
  const items = useMemo(() => all.filter((q) => !q.expires_at || q.expires_at > now), [all, now]);
  return { items, done };
}

/**
 * Everything waiting on a person, for the sidebar badge and the top bar: pending approvals
 * (seeded from `GET /api/permissions/pending`, then `permission.requested` / `permission.replied`)
 * plus pending questions. MCP link cards (`mcp.elicitation.open`) only appear after the consent
 * question here was answered, so they are not counted again.
 */
export function useNeedsYou(): { approvals: number; questions: number; total: number } {
  const control = useControl();
  const ready = useSeedReady();
  const [ids, setIds] = useState<ReadonlySet<string>>(new Set());
  const { items } = usePendingQuestions();

  useEffect(() => {
    if (!ready) return;
    let cancelled = false;
    api
      .pendingPermissions()
      .then((p) => !cancelled && setIds(new Set(p.map((x) => x.request_id))))
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [ready]);

  useEffect(
    () =>
      control.listen((e) => {
        const rid = String(e.data.request_id ?? "");
        if (!rid) return;
        if (e.type === "permission.requested") setIds((s) => new Set(s).add(rid));
        else if (e.type === "permission.replied")
          setIds((s) => {
            const n = new Set(s);
            n.delete(rid);
            return n;
          });
      }),
    [control],
  );

  return { approvals: ids.size, questions: items.length, total: ids.size + items.length };
}
