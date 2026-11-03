import type { PendingPermission, SessionInfo } from "@sdk/client";
import type { QuestionAskedData } from "@sdk/events";
import { RefreshCwIcon, ScrollTextIcon } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router";
import { ApprovalCard } from "@/components/ApprovalCard";
import { EmptyState, ErrorNote, PageHeader } from "@/components/AppShell";
import { Badge } from "@/components/ui/badge";
import { QuestionTabs } from "@/components/QuestionTabs";
import { Button } from "@/components/ui/button";
import { useApprovals } from "@/hooks/useApprovals";
import { usePendingQuestions } from "@/hooks/useNeedsYou";
import { api } from "@/lib/api";
import { useControl } from "@/lib/controlContext";

interface Decision {
  request_id: string;
  session_id: string;
  reply: string;
  tool: string;
  cascade: boolean;
  at: string;
}

/** Approval group key (spec §25.10; the server adds the cwd root). */
const groupKey = (p: PendingPermission) => `${p.permission}\u0000${p.risk ?? "medium"}\u0000${p.tool}`;

/** Audit rows for one decision: the session's `permission.reply` entries. */
const auditHref = (sessionId: string) =>
  `/audit?session_id=${encodeURIComponent(sessionId)}&action=permission.reply`;

/**
 * The inbox: everything an agent is waiting on. Approvals are a cross-session queue read from
 * the shared approvals store (`useApprovals`: one `GET /api/permissions/pending` per control
 * connection, then live WS `permission.requested/replied` events; the shared connection tails
 * from `GET /api/events/latest`, so history is never replayed) plus the `connected` frame's
 * pending list. A 3s REST poll runs only while the WebSocket is down.
 */
export function ApprovalsPage() {
  const control = useControl();
  const wsUp = control.state === "open";
  const { items: pending, error, refresh, remove } = useApprovals();
  const [sessions, setSessions] = useState<Record<string, SessionInfo>>({});
  const [answered, setAnswered] = useState<ReadonlySet<string>>(new Set());
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const questions = usePendingQuestions();
  const questionsBySession = useMemo(() => {
    const m = new Map<string, QuestionAskedData[]>();
    for (const q of questions.items) {
      const ask = { request_id: q.request_id, questions: q.questions, timeout_s: q.timeout_s ?? undefined, expires_at: q.expires_at ?? undefined };
      m.set(q.session_id, [...(m.get(q.session_id) ?? []), ask as unknown as QuestionAskedData]);
    }
    return [...m.entries()];
  }, [questions.items]);
  const toolOf = useRef(new Map<string, string>()); // request id → tool, for the decisions list
  const markAnswered = useCallback(
    (rid: string) => {
      setAnswered((a) => new Set(a).add(rid));
      remove(rid);
    },
    [remove],
  );

  useEffect(() => {
    api
      .listSessions(500)
      .then((s) => setSessions(Object.fromEntries(s.map((x) => [x.id, x]))))
      .catch(() => undefined);
  }, [pending.length, questions.items.length]);

  useEffect(
    () =>
      control.listen((e) => {
        if (e.type === "permission.replied") {
          const rid = String(e.data.request_id);
          const d: Decision = {
            request_id: rid,
            session_id: e.aggregate_id,
            reply: String(e.data.reply ?? ""),
            tool: toolOf.current.get(rid) ?? "",
            cascade: e.data.cascade === true,
            at: e.created_at,
          };
          setDecisions((ds) => [d, ...ds.filter((x) => x.request_id !== rid)].slice(0, 20));
          markAnswered(rid);
        }
      }),
    [control, markAnswered],
  );

  // Pending requests re-sent in the WS `connected` frame, minus anything answered since.
  const groups = useMemo(() => {
    const all = [...pending];
    const have = new Set(all.map((x) => x.request_id));
    for (const x of (control.connected?.pending_permissions ?? []) as unknown as PendingPermission[]) {
      if (!have.has(x.request_id) && !answered.has(x.request_id)) all.push(x);
    }
    const g = new Map<string, PendingPermission[]>();
    for (const p of all) g.set(p.session_id, [...(g.get(p.session_id) ?? []), p]);
    const matching = new Map<string, number>();
    for (const p of all) matching.set(groupKey(p), (matching.get(groupKey(p)) ?? 0) + 1);
    return { bySession: [...g.entries()], matching, all };
  }, [pending, answered, control.connected]);

  useEffect(() => {
    for (const p of groups.all) toolOf.current.set(p.request_id, p.tool);
  }, [groups]);

  return (
    <div className="flex flex-col">
      <PageHeader
        title="Inbox"
        description={
          <>
            Agents pause here before risky actions and when they need an answer. Nothing runs until you decide.{" "}
            <span data-testid="approvals-live">{wsUp ? "Live over WebSocket." : "Polling every 3s (WebSocket down)."}</span>
          </>
        }
        actions={
          <Button variant="outline" size="icon" aria-label="Refresh approvals" onClick={refresh}>
            <RefreshCwIcon />
          </Button>
        }
      />
      <div className="flex flex-wrap items-start gap-6 page-body">
        <section aria-labelledby="waiting" className="flex min-w-0 flex-[999_1_520px] flex-col gap-3.5">
          <h2 id="waiting" className="flex items-center gap-2.5 text-title font-semibold">
            Waiting for you
            {groups.all.length + questions.items.length > 0 && (
              <span className="rounded-full bg-warning px-2 text-xs leading-5 font-semibold text-card">
                {groups.all.length + questions.items.length}
              </span>
            )}
          </h2>
          <ErrorNote error={error} />
          {groups.bySession.length === 0 && questionsBySession.length === 0 && (
            <EmptyState title="All clear">Approvals and questions show up here as they arrive.</EmptyState>
          )}
          {questionsBySession.map(([sid, asks]) => (
            <section
              key={`q-${sid}`}
              aria-labelledby={`qgrp-${sid}`}
              className="overflow-hidden rounded-xl border bg-card shadow-card"
              data-testid="question-group"
            >
              <h3 id={`qgrp-${sid}`} className="flex flex-wrap items-center gap-2 border-b border-border-subtle bg-muted/40 px-4 py-2.5 text-sm font-medium">
                <Link to={`/session/${sid}`} className="text-brand-ink hover:underline">
                  {sessions[sid]?.title || "Untitled session"}
                </Link>
                <span className="font-mono text-xs text-muted-foreground">{sid}</span>
                <Badge variant="info" className="ml-auto">
                  {asks.length} question{asks.length === 1 ? "" : "s"}
                </Badge>
              </h3>
              <div className="p-3">
                <QuestionTabs pending={asks} onDone={questions.done} />
              </div>
            </section>
          ))}
          {groups.bySession.map(([sid, items]) => (
            <section
              key={sid}
              aria-labelledby={`grp-${sid}`}
              className="overflow-hidden rounded-lg border bg-card"
              data-testid="approval-group"
            >
              <h3 id={`grp-${sid}`} className="flex flex-wrap items-center gap-2 border-b border-border-subtle bg-muted/40 px-4 py-2.5 text-sm font-medium">
                <Link to={`/session/${sid}`} className="text-brand-ink hover:underline">
                  {sessions[sid]?.title || "Untitled session"}
                </Link>
                <span className="font-mono text-xs text-muted-foreground">{sid}</span>
                <Badge variant="warning" className="ml-auto">{items.length} waiting</Badge>
              </h3>
              <div className="flex flex-col gap-3 p-3">
                {items.map((p) => (
                  <ApprovalCard
                    key={p.request_id}
                    compact
                    request={p}
                    matching={groups.matching.get(groupKey(p)) ?? 1}
                    onDone={() => markAnswered(p.request_id)}
                  />
                ))}
              </div>
            </section>
          ))}
        </section>
        <aside aria-labelledby="recent-decisions" className="flex min-w-0 flex-[1_1_300px] flex-col gap-3.5" data-testid="recent-decisions">
          <h2 id="recent-decisions" className="text-title font-semibold">
            Recent decisions
          </h2>
          {decisions.length === 0 ? (
            <p className="rounded-lg border bg-card px-4 py-3 text-sm text-muted-foreground">Decisions you make while this page is open appear here.</p>
          ) : (
            <ul className="rounded-lg border bg-card">
              {decisions.map((d) => (
                <li key={d.request_id} className="flex items-start gap-3 border-b border-border-subtle px-3.5 py-3 last:border-0" data-testid="decision-row">
                  <Badge variant={d.reply === "reject" ? "destructive" : d.reply === "always" ? "brand" : "success"} className="min-w-18 justify-center">
                    {d.reply}
                  </Badge>
                  <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                    <span className="truncate font-mono text-xs">
                      {d.tool || d.request_id.slice(0, 8)}
                      {d.cascade && <span className="ml-1.5 font-sans text-muted-foreground">(bulk)</span>}
                    </span>
                    <Link to={`/session/${d.session_id}`} className="truncate text-xs text-muted-foreground hover:underline">
                      {sessions[d.session_id]?.title || d.session_id}
                    </Link>
                  </div>
                  <Link to={auditHref(d.session_id)} className="inline-flex items-center gap-1 text-xs whitespace-nowrap text-brand-ink hover:underline" data-testid="audit-link">
                    <ScrollTextIcon aria-hidden className="size-3" /> Audit
                  </Link>
                </li>
              ))}
            </ul>
          )}
          <p className="text-xs text-muted-foreground">
            “Always” rules live in your permissions files; every decision is kept in the{" "}
            <Link to="/audit?action=permission.reply" className="text-brand-ink underline underline-offset-2 hover:decoration-2">audit log</Link>.
          </p>
        </aside>
      </div>
    </div>
  );
}
