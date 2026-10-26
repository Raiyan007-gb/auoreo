import type { ControlState, PermissionReplyKind, ServerEvent, WsConnected } from "@sdk/client";
import { createContext, use } from "react";

export type Listener = (e: ServerEvent) => void;

export interface ControlApi {
  state: ControlState;
  /** Last `connected` frame (pending permissions/questions re-sent on (re)connect). */
  connected: WsConnected | null;
  /** WS `control_response` first; falls back to `POST /api/permissions/{id}/reply`. */
  replyPermission: (
    requestId: string,
    reply: PermissionReplyKind,
    message?: string,
    cascade?: boolean,
  ) => Promise<"ws" | "rest">;
  /** WS `control_response{question}` first; falls back to `POST /api/questions/{id}/answer`. */
  answerQuestion: (requestId: string, responses: Record<string, unknown>[]) => Promise<"ws" | "rest">;
  /** WS `control_request{steer}`; falls back to `POST /api/chat {mode: "steer"}`. */
  steer: (sessionId: string, text: string) => Promise<"ws" | "rest">;
  /** WS `control_request{interrupt}`; falls back to `POST /api/sessions/{id}/cancel`. */
  interrupt: (sessionId: string) => Promise<"ws" | "rest">;
  /** Live ServerEvents that arrive over the WS (all channels). */
  listen: (fn: Listener) => () => void;
}

export const ControlContext = createContext<ControlApi | null>(null);

export function useControl(): ControlApi {
  const v = use(ControlContext);
  if (!v) throw new Error("useControl outside ControlProvider");
  return v;
}
