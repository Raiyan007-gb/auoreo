import type { MessageOut, ServerEvent, SessionDetail } from "@sdk/client";
import { TERMINAL_RUN_EVENTS } from "@sdk/client";
import type { PermissionRequestedData, QuestionAskedData } from "@sdk/events";
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import { useEventHub } from "@/lib/eventsHub";
import { emptyThread, fromMessages, type Item, reduceEvents, type ThreadState } from "@/lib/thread";

export interface ThreadView {
  session: SessionDetail | undefined;
  items: Item[];
  state: ThreadState;
  /** True once the event replay has caught up (the thread is event-derived from then on). */
  live: boolean;
  streamOk: boolean;
  error: unknown;
  refreshSession: () => void;
  dismissPermission: (id: string) => void;
  dismissQuestion: (id: string) => void;
  dismissElicitation: (id: string) => void;
}

/**
 * Thread for one session: first paint from `/messages`, then the shared tab event stream
 * replays this session from seq 0 (events are the source of truth) and tails live. Events
 * are batched per animation frame; the replay is flushed in one batch when it completes.
 */
export function useThread(sessionId: string): ThreadView {
  const [session, setSession] = useState<SessionDetail | undefined>(undefined);
  const [snapshot, setSnapshot] = useState<MessageOut[] | null>(null);
  const [state, setState] = useState<ThreadState>(emptyThread);
  const [live, setLive] = useState(false);
  const [streamOk, setStreamOk] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [sessionTick, setSessionTick] = useState(0);
  const buffer = useRef<ServerEvent[]>([]);
  const hub = useEventHub();

  useEffect(() => {
    let cancelled = false;
    api
      .getSession(sessionId)
      .then((s) => !cancelled && setSession(s))
      .catch((e: unknown) => !cancelled && setError(e));
    return () => {
      cancelled = true;
    };
  }, [sessionId, sessionTick]);

  useEffect(() => {
    // The page is keyed by session id (App.tsx), so state starts fresh per session.
    let cancelled = false;
    buffer.current = [];
    api
      .messages(sessionId)
      .then((m) => !cancelled && setSnapshot(m))
      .catch((e: unknown) => !cancelled && setError(e));

    let isLive = false;
    let raf = 0;
    let maxWait: ReturnType<typeof setTimeout> | null = null;

    const flush = () => {
      raf = 0;
      const batch = buffer.current;
      if (!batch.length) return;
      buffer.current = [];
      setState((s) => reduceEvents(s, batch));
      if (batch.some((e) => TERMINAL_RUN_EVENTS.has(e.type) || e.type === "run.started")) {
        setSessionTick((t) => t + 1);
      }
    };
    const goLive = () => {
      if (isLive || cancelled) return;
      isLive = true;
      if (maxWait) clearTimeout(maxWait);
      flush();
      setLive(true);
      void reconcile();
    };
    const reconcile = async () => {
      try {
        const [perms, qs] = await Promise.all([
          api.pendingPermissions(sessionId),
          api.pendingQuestions(sessionId),
        ]);
        if (cancelled) return;
        setState((s) => ({
          ...s,
          permissions: Object.fromEntries(
            perms.map((p) => [p.request_id, (s.permissions[p.request_id] ?? p) as PermissionRequestedData]),
          ),
          questions: Object.fromEntries(
            qs.map((q) => [q.request_id, (s.questions[q.request_id] ?? q) as unknown as QuestionAskedData]),
          ),
        }));
      } catch {
        /* keep event-derived state */
      }
    };

    // Shared tab stream (EventsContext): replay this session from seq 0, then tail live.
    maxWait = setTimeout(goLive, 5000); // never stay on the snapshot if the replay hangs
    const unsubscribe = hub.subscribe(
      { channel: sessionId, afterSeq: 0 },
      (e) => {
        buffer.current.push(e);
        if (isLive) raf ||= requestAnimationFrame(flush);
      },
      {
        onOpen: () => setStreamOk(true),
        onError: () => setStreamOk(false),
        onReplayed: goLive,
      },
    );
    return () => {
      cancelled = true;
      unsubscribe();
      if (raf) cancelAnimationFrame(raf);
      if (maxWait) clearTimeout(maxWait);
    };
  }, [sessionId, hub]);

  const items = useMemo(() => {
    // A fork's inherited history lives only in `/messages` (no events in this session).
    const inherited =
      state.forkedAt !== undefined && snapshot ? fromMessages(snapshot.slice(0, state.forkedAt + 1)) : [];
    if (live && (state.items.length > 0 || !snapshot?.length)) return inherited.length ? [...inherited, ...state.items] : state.items;
    return snapshot ? fromMessages(snapshot) : [];
  }, [live, state.items, state.forkedAt, snapshot]);

  return {
    session,
    items,
    state,
    live,
    streamOk,
    error,
    refreshSession: () => setSessionTick((t) => t + 1),
    dismissPermission: (id) =>
      setState((s) => {
        const { [id]: _gone, ...rest } = s.permissions;
        return { ...s, permissions: rest };
      }),
    dismissQuestion: (id) =>
      setState((s) => {
        const { [id]: _gone, ...rest } = s.questions;
        return { ...s, questions: rest };
      }),
    dismissElicitation: (id) =>
      setState((s) => {
        const { [id]: _gone, ...rest } = s.elicitations;
        return { ...s, elicitations: rest };
      }),
  };
}
