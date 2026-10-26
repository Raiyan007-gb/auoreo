import type { ControlConnection, ControlState, WsConnected } from "@sdk/client";
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import { type ControlApi, ControlContext, type Listener } from "@/lib/controlContext";

/** One shared WebSocket for the whole app; everything degrades to REST while it is down. */
export function ControlProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<ControlState>("connecting");
  const [connected, setConnected] = useState<WsConnected | null>(null);
  const conn = useRef<ControlConnection | null>(null);
  const listeners = useRef(new Set<Listener>());

  useEffect(() => {
    // Live tail: seed `hello.last_seq` with the current head (`GET /api/events/latest`) so
    // the WS never replays history; if that fails, connect live-only (no last_seq).
    let c: ControlConnection | null = null;
    let cancelled = false;
    const start = (lastSeq?: number) => {
      if (cancelled) return;
      c = api.connectControl({
        channels: null,
        lastSeq,
        onState: setState,
        onConnected: setConnected,
        onEvent: (e) => {
          for (const fn of listeners.current) fn(e);
        },
      });
      conn.current = c;
    };
    api
      .latestSeq()
      .then((r) => start(r.seq))
      .catch(() => start());
    return () => {
      cancelled = true;
      c?.close();
      conn.current = null;
    };
  }, []);

  const value = useMemo<ControlApi>(() => {
    const ws = () => (conn.current?.state() === "open" ? conn.current : null);
    return {
      state,
      connected,
      async replyPermission(requestId, reply, message, cascade = false) {
        const c = ws();
        if (c && (await c.respond(requestId, { kind: "permission", reply, message: message ?? null, cascade })))
          return "ws";
        await api.replyPermission(requestId, reply, message ?? null, cascade);
        return "rest";
      },
      async answerQuestion(requestId, responses) {
        const c = ws();
        if (c && (await c.respond(requestId, { kind: "question", responses }))) return "ws";
        await api.answerQuestion(requestId, responses);
        return "rest";
      },
      async steer(sessionId, text) {
        const c = ws();
        if (c && (await c.control({ kind: "steer", session_id: sessionId, text }))) return "ws";
        await api.chat({ session_id: sessionId, message: text, mode: "steer" });
        return "rest";
      },
      async interrupt(sessionId) {
        const c = ws();
        if (c && (await c.control({ kind: "interrupt", session_id: sessionId }))) return "ws";
        await api.cancel(sessionId);
        return "rest";
      },
      listen(fn) {
        listeners.current.add(fn);
        return () => listeners.current.delete(fn);
      },
    };
  }, [state, connected]);

  return <ControlContext value={value}>{children}</ControlContext>;
}
