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
    const c = api.connectControl({
      channels: null,
      onState: setState,
      onConnected: setConnected,
      onEvent: (e) => {
        for (const fn of listeners.current) fn(e);
      },
    });
    conn.current = c;
    return () => {
      c.close();
      conn.current = null;
    };
  }, []);

  const value = useMemo<ControlApi>(() => {
    const ws = () => (conn.current?.state() === "open" ? conn.current : null);
    return {
      state,
      connected,
      async replyPermission(requestId, reply, message) {
        const c = ws();
        if (c && (await c.respond(requestId, { kind: "permission", reply, message: message ?? null })))
          return "ws";
        await api.replyPermission(requestId, reply, message ?? null);
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
