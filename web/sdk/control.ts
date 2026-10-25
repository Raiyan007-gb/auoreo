/**
 * WebSocket control plane (`/ws`) — frames mirror src/auoreo/schema/ws.py exactly.
 * Re-exported from `client.ts`; kept separate to stay under the 500-line file budget.
 */
import type { PermissionReplyKind, ServerEvent } from "./client";

function wsQuery(q: Record<string, string | number | null | undefined>): string {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(q)) if (v !== undefined && v !== null && v !== "") qs.append(k, String(v));
  const out = qs.toString();
  return out ? `?${out}` : "";
}

// Frames mirror src/auoreo/schema/ws.py exactly.
export interface WsHello {
  type: "hello";
  last_seq?: number | null;
  channels?: string[] | null;
}
export interface WsSubscribe {
  type: "subscribe";
  channels: string[];
  last_seq?: number | null;
}
export interface WsPong {
  type: "pong";
  tick: number;
}
export type WsPermissionReply = {
  kind: "permission";
  reply: PermissionReplyKind;
  message?: string | null;
};
export type WsQuestionAnswer = { kind: "question"; responses: Record<string, unknown>[] };
export interface WsControlResponse {
  type: "control_response";
  request_id: string;
  response: WsPermissionReply | WsQuestionAnswer;
}
export type WsControlRequestBody =
  | { kind: "interrupt"; session_id: string }
  | { kind: "steer"; session_id: string; text: string }
  | { kind: "followup"; session_id: string; text: string; agent?: string | null; model?: string | null };
export interface WsControlRequest {
  type: "control_request";
  request_id: string;
  request: WsControlRequestBody;
}
export type ClientFrame = WsHello | WsSubscribe | WsPong | WsControlResponse | WsControlRequest;

export interface WsConnected {
  type: "connected";
  server_seq: number;
  generation: number;
  pending_permissions: Record<string, unknown>[];
  pending_questions: Record<string, unknown>[];
}
export interface WsPing {
  type: "ping";
  tick: number;
  server_seq: number;
}
export interface WsAck {
  type: "ack";
  request_id: string;
  ok: boolean;
  detail?: string | null;
}
export interface WsError {
  type: "error";
  subtype: "unknown_type" | "invalid_frame" | "binary_dropped" | "unauthorized" | "not_found";
  message: string;
  request_id?: string | null;
}

export const WS_CLOSE = {
  OK: 1000,
  GOING_AWAY: 1001,
  POLICY: 1008,
  KEEPALIVE: 1011,
  TRY_LATER: 1013,
  AUTH_EXPIRED: 4001, // transient: re-authenticate, at most 3 retries
  UNAUTHORIZED: 4003,
  REVOKED: 4401, // terminal
} as const;

export type ControlState = "connecting" | "open" | "reconnecting" | "unavailable" | "closed";

export interface ControlOptions {
  readonly baseUrl?: string;
  readonly token?: string | null;
  readonly channels?: string[] | null;
  /** Replay events after this seq; omit for live-only (pending requests arrive in `connected`). */
  readonly lastSeq?: number;
  readonly onEvent?: (e: ServerEvent) => void;
  readonly onConnected?: (f: WsConnected) => void;
  readonly onState?: (s: ControlState) => void;
  readonly onError?: (f: WsError) => void;
  /** Reconnect delay base (ms) and attempts: 2s × 5 by default. */
  readonly retryDelayMs?: number;
  readonly maxRetries?: number;
  readonly ackTimeoutMs?: number;
  /** After `maxRetries`, keep probing at this interval while showing "unavailable". */
  readonly slowRetryMs?: number;
}

export interface ControlConnection {
  readonly state: () => ControlState;
  /** Answer a server-initiated request. Resolves true on `ack{ok}`, false otherwise. */
  readonly respond: (requestId: string, response: WsControlResponse["response"]) => Promise<boolean>;
  /** Client-initiated control (interrupt/steer/followup). */
  readonly control: (request: WsControlRequestBody) => Promise<boolean>;
  readonly subscribe: (channels: string[]) => void;
  readonly close: () => void;
}

function wsUrl(baseUrl: string | undefined, query: string): string {
  const origin =
    baseUrl && /^https?:/.test(baseUrl) ? baseUrl : `${location.protocol}//${location.host}`;
  return `${origin.replace(/^http/, "ws")}/ws${query}`;
}

function newRequestId(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

/**
 * Connect the WebSocket control plane: send `hello{last_seq, channels}`, answer `ping` with
 * `pong`, resolve `ack`s, forward ServerEvent frames (deduped by seq), reconnect with
 * linear backoff (2s × attempt, 5 attempts), never retry after 4003/4401/1008 and at most
 * 3 times after 4001 (auth expired). `respond()` is
 * idempotent per `request_id` (a second answer for the same id is never sent).
 */
export function connectControl(opts: ControlOptions): ControlConnection {
  const delay = opts.retryDelayMs ?? 2000;
  const maxRetries = opts.maxRetries ?? 5;
  const ackTimeout = opts.ackTimeoutMs ?? 4000;
  const slowRetry = opts.slowRetryMs ?? 30_000;
  let ws: WebSocket | null = null;
  let state: ControlState = "connecting";
  let attempts = 0;
  let authRetries = 0;
  // null = live only (no replay) on the first connect; afterwards the last seen seq.
  let lastSeq: number | null = opts.lastSeq ?? null;
  let channels = opts.channels ?? null;
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | null = null;
  const waiting = new Map<string, (ok: boolean) => void>();
  const answered = new Map<string, Promise<boolean>>();

  const setState = (s: ControlState) => {
    state = s;
    opts.onState?.(s);
  };
  const send = (f: ClientFrame): boolean => {
    if (ws?.readyState !== WebSocket.OPEN) return false;
    ws.send(JSON.stringify(f));
    return true;
  };
  const settleAll = (ok: boolean) => {
    for (const fn of waiting.values()) fn(ok);
    waiting.clear();
  };

  const handle = (frame: Record<string, unknown>) => {
    const type = frame.type;
    if (typeof frame.seq === "number" && typeof frame.aggregate_id === "string") {
      const ev = frame as unknown as ServerEvent;
      if (lastSeq !== null && ev.seq <= lastSeq) return;
      lastSeq = ev.seq;
      opts.onEvent?.(ev);
      return;
    }
    switch (type) {
      case "connected": {
        const c = frame as unknown as WsConnected;
        attempts = 0;
        // Live-only connect: remember where "now" was so a reconnect replays the gap.
        if (lastSeq === null && typeof c.server_seq === "number") lastSeq = c.server_seq;
        setState("open");
        opts.onConnected?.(c);
        return;
      }
      case "ping":
        send({ type: "pong", tick: Number(frame.tick ?? 0) });
        return;
      case "ack": {
        const a = frame as unknown as WsAck;
        waiting.get(a.request_id)?.(a.ok);
        waiting.delete(a.request_id);
        return;
      }
      case "error": {
        const e = frame as unknown as WsError;
        if (e.request_id) {
          waiting.get(e.request_id)?.(false);
          waiting.delete(e.request_id);
        }
        opts.onError?.(e);
        return;
      }
      default:
        return;
    }
  };

  const open = () => {
    if (stopped) return;
    const q = wsQuery({ token: opts.token, last_seq: lastSeq });
    let sock: WebSocket;
    try {
      sock = new WebSocket(wsUrl(opts.baseUrl, q));
    } catch {
      scheduleRetry();
      return;
    }
    ws = sock;
    sock.onopen = () => {
      send({ type: "hello", last_seq: lastSeq, channels });
    };
    sock.onmessage = (m: MessageEvent) => {
      if (typeof m.data !== "string") return;
      for (const line of m.data.split("\n")) {
        if (!line.trim()) continue;
        try {
          handle(JSON.parse(line) as Record<string, unknown>);
        } catch {
          /* ignore malformed frame */
        }
      }
    };
    sock.onclose = (c: CloseEvent) => {
      ws = null;
      settleAll(false);
      if (stopped) return setState("closed");
      if (c.code === WS_CLOSE.UNAUTHORIZED || c.code === WS_CLOSE.POLICY || c.code === WS_CLOSE.REVOKED) {
        return setState("unavailable");
      }
      if (c.code === WS_CLOSE.AUTH_EXPIRED && ++authRetries > 3) {
        return setState("unavailable");
      }
      scheduleRetry();
    };
  };

  const scheduleRetry = () => {
    if (stopped) return;
    if (attempts >= maxRetries) {
      // Out of fast retries: stay "unavailable" (REST fallbacks) but keep probing slowly.
      setState("unavailable");
      timer = setTimeout(open, slowRetry);
      return;
    }
    attempts += 1;
    setState("reconnecting");
    timer = setTimeout(open, delay * attempts);
  };

  const awaitAck = (requestId: string): Promise<boolean> =>
    new Promise<boolean>((resolve) => {
      const t = setTimeout(() => {
        waiting.delete(requestId);
        resolve(false);
      }, ackTimeout);
      waiting.set(requestId, (ok) => {
        clearTimeout(t);
        resolve(ok);
      });
    });

  open();

  return {
    state: () => state,
    respond(requestId, response) {
      const prior = answered.get(requestId);
      if (prior) return prior;
      if (state !== "open") return Promise.resolve(false);
      const p = awaitAck(requestId);
      if (!send({ type: "control_response", request_id: requestId, response })) {
        waiting.get(requestId)?.(false);
        waiting.delete(requestId);
        return Promise.resolve(false);
      }
      // Only remember successful answers, so a failed one can fall back / be retried.
      const remembered = p.then((ok) => {
        if (!ok) answered.delete(requestId);
        return ok;
      });
      answered.set(requestId, remembered);
      return remembered;
    },
    control(request) {
      if (state !== "open") return Promise.resolve(false);
      const id = newRequestId();
      const p = awaitAck(id);
      if (!send({ type: "control_request", request_id: id, request })) return Promise.resolve(false);
      return p;
    },
    subscribe(next) {
      channels = next;
      send({ type: "subscribe", channels: next, last_seq: lastSeq });
    },
    close() {
      stopped = true;
      if (timer) clearTimeout(timer);
      ws?.close(WS_CLOSE.OK);
      setState("closed");
    },
  };
}
