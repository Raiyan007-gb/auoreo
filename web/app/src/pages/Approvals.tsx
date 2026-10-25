import type { PendingPermission, SessionInfo } from "@sdk/client";
import { RefreshCwIcon } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router";
import { ApprovalCard } from "@/components/ApprovalCard";
import { EmptyState, ErrorNote, PageHeader } from "@/components/AppShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { useControl } from "@/lib/controlContext";

const POLL_MS = 3000;

/**
 * Cross-session approval queue: `GET /api/permissions/pending` once, then kept live by WS
 * `permission.requested/replied` events (the shared connection tails from
 * `GET /api/events/latest`, so history is never replayed) and the `connected` frame's
 * pending list. A 3s REST poll runs only while the WebSocket is down.
 */
export function ApprovalsPage() {
  const control = useControl();
  const wsUp = control.state === "open";
  const [pending, setPending] = useState<PendingPermission[]>([]);
  const [sessions, setSessions] = useState<Record<string, SessionInfo>>({});
  const [error, setError] = useState<unknown>(null);
  const [tick, setTick] = useState(0);
  const [answered, setAnswered] = useState<ReadonlySet<string>>(new Set());
  const markAnswered = (rid: string) => {
    setAnswered((a) => new Set(a).add(rid));
    setPending((p) => p.filter((x) => x.request_id !== rid));
  };

  useEffect(() => {
    let cancelled = false;
    const load = () =>
      api
        .pendingPermissions()
        .then((p) => {
          if (cancelled) return;
          setPending(p);
          setError(null);
        })
        .catch((e: unknown) => !cancelled && setError(e));
    void load();
    // WS up: live permission.* events keep the list current (re-seeded on every `connected`).
    // WS down: fall back to a 3s REST poll.
    const t = wsUp ? null : setInterval(load, POLL_MS);
    return () => {
      cancelled = true;
      if (t) clearInterval(t);
    };
  }, [tick, wsUp, control.connected]);

  useEffect(() => {
    api
      .listSessions(500)
      .then((s) => setSessions(Object.fromEntries(s.map((x) => [x.id, x]))))
      .catch(() => undefined);
  }, [pending.length]);

  useEffect(
    () =>
      control.listen((e) => {
        if (e.type === "permission.requested") {
          const d = e.data as unknown as Omit<PendingPermission, "session_id">;
          setPending((p) =>
            p.some((x) => x.request_id === d.request_id) ? p : [...p, { ...d, session_id: e.aggregate_id }],
          );
        } else if (e.type === "permission.replied") {
          markAnswered(String(e.data.request_id));
        }
      }),
    [control],
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
    return [...g.entries()];
  }, [pending, answered, control.connected]);

  return (
    <div className="flex flex-col">
      <PageHeader
        title="Approvals"
        description={
          <>
            Tool calls waiting for a human decision, across all sessions.{" "}
            <span data-testid="approvals-live">{wsUp ? "Live over WebSocket." : "Polling every 3s (WebSocket down)."}</span>
          </>
        }
        actions={
          <Button variant="outline" size="icon" aria-label="Refresh approvals" onClick={() => setTick((t) => t + 1)}>
            <RefreshCwIcon />
          </Button>
        }
      />
      <div className="space-y-4 p-4">
        <ErrorNote error={error} />
        {groups.length === 0 && <EmptyState title="Nothing to approve">New requests show up here as they arrive.</EmptyState>}
        {groups.map(([sid, items]) => (
          <section key={sid} aria-labelledby={`grp-${sid}`} className="space-y-2" data-testid="approval-group">
            <h2 id={`grp-${sid}`} className="flex items-center gap-2 text-sm font-medium">
              <Link to={`/session/${sid}`} className="hover:underline">
                {sessions[sid]?.title || "Untitled session"}
              </Link>
              <span className="font-mono text-xs text-muted-foreground">{sid}</span>
              <Badge variant="warning">{items.length}</Badge>
            </h2>
            {items.map((p) => (
              <ApprovalCard
                key={p.request_id}
                compact
                request={p}
                onDone={() => markAnswered(p.request_id)}
              />
            ))}
          </section>
        ))}
      </div>
    </div>
  );
}
