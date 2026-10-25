/**
 * One shared `EventSource(/api/events)` per tab (spec §17 `EventsContext`), fanned out to
 * subscribers by channel (`aggregate_id`: a session id or `"server"`).
 *
 * - The live stream starts at the current latest seq (`GET /api/events/latest`) and follows
 *   every channel. The browser reconnects on its own (server `retry: 3000`) and re-sends
 *   `Last-Event-ID`; if it gives up (readyState CLOSED) we reopen from the last seq after 3s.
 * - A subscriber that wants history (`afterSeq: number`) gets a one-shot replay of its channel
 *   (`?follow=false`, plain `fetch`, so it can carry the bearer header) while its live events
 *   queue; the queue is flushed after the replay. Every subscriber de-duplicates by `seq`.
 * - EventSource cannot send headers: same-origin cookie auth works as is; until cookie auth is
 *   everywhere, a token (meta / Settings) is also passed as `?token=` on the live stream.
 */
import { EVENT_TYPES, type ServerEvent } from "@sdk/client";
import { createContext, useContext, useEffect, useRef } from "react";
import { getToken } from "@/lib/api";

/** Event names beyond the SDK's `EVENT_TYPES` that UI pages listen for. */
export const EXTRA_EVENT_TYPES = [
  "plugin.started",
  "plugin.stopped",
  "plugin.restarted",
  "plugin.crashed",
  "plugin.invalid_config",
  "plugin.paired",
  "cron.fired",
  "cron.delivered",
  "cron.failed",
  "delivery.queued",
  "delivery.sent",
  "delivery.delivered",
  "delivery.failed",
  "delivery.unknown",
  "policy.choice",
  "voice.transcript",
  "voice.speaking",
  "voice.spoke",
  "voice.barge_in",
  "voice.speech_cancelled",
  "voice.error",
  "rtc.audio_level",
  "rtc.closed",
  "rtc.connected",
] as const;

export interface HubSubscription {
  /** `aggregate_id` to receive (session id or `"server"`); `null` = every channel. */
  readonly channel: string | null;
  /** Replay this channel after `afterSeq` first; `"live"` (default) = only new events. */
  readonly afterSeq?: number | "live";
  /** Extra SSE event names this subscriber needs (EventSource needs a listener per name). */
  readonly types?: readonly string[];
}

export interface HubHandlers {
  readonly onOpen?: () => void;
  readonly onError?: () => void;
  /** The replay (if any) has been delivered; live events follow. */
  readonly onReplayed?: () => void;
}

interface Sub {
  readonly channel: string | null;
  readonly cb: (e: ServerEvent) => void;
  readonly h: HubHandlers;
  lastSeq: number;
  queue: ServerEvent[] | null; // non-null while replaying
}

const RETRY_MS = 3000;
const IDLE_CLOSE_MS = 5000;

/** Parse a complete `text/event-stream` body into envelopes (comments/retry ignored). */
export function parseSse(text: string): ServerEvent[] {
  const out: ServerEvent[] = [];
  for (const block of text.split(/\r?\n\r?\n/)) {
    const data = block
      .split(/\r?\n/)
      .filter((l) => l.startsWith("data:"))
      .map((l) => l.slice(5).replace(/^ /, ""))
      .join("\n");
    if (!data) continue;
    try {
      const ev = JSON.parse(data) as ServerEvent;
      if (typeof ev.seq === "number") out.push(ev);
    } catch {
      /* not an envelope */
    }
  }
  return out;
}

export class EventHub {
  private es: EventSource | null = null;
  private subs = new Set<Sub>();
  private types = new Set<string>([...EVENT_TYPES, ...EXTRA_EVENT_TYPES]);
  private listening = new Set<string>();
  private cursor = -1; // -1 = not started
  private timer: ReturnType<typeof setTimeout> | null = null;
  private idle: ReturnType<typeof setTimeout> | null = null;
  private starting = false;
  open = false;

  constructor(
    private readonly baseUrl: string,
    private readonly getToken: () => string | null,
  ) {}

  subscribe(opts: HubSubscription, cb: (e: ServerEvent) => void, h: HubHandlers = {}): () => void {
    const replay = typeof opts.afterSeq === "number";
    const sub: Sub = { channel: opts.channel, cb, h, lastSeq: replay ? (opts.afterSeq as number) : 0, queue: replay ? [] : null };
    if (!replay) sub.lastSeq = Math.max(0, this.cursor);
    this.subs.add(sub);
    for (const t of opts.types ?? []) this.addType(t);
    if (this.idle) clearTimeout(this.idle);
    this.idle = null;
    void this.ensure();
    if (this.open) h.onOpen?.();
    if (replay) void this.replay(sub, false);
    return () => {
      this.subs.delete(sub);
      if (!this.subs.size) this.idle = setTimeout(() => this.close(), IDLE_CLOSE_MS);
    };
  }

  /** Highest seq seen on the live stream (-1 before it started). */
  get seq(): number {
    return this.cursor;
  }

  close(): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    this.es?.close();
    this.es = null;
    this.open = false;
    this.cursor = -1;
    this.listening.clear();
  }

  private addType(t: string): void {
    if (this.types.has(t)) return;
    this.types.add(t);
    if (this.es) this.listen(this.es, t);
  }

  private listen(es: EventSource, t: string): void {
    if (this.listening.has(t)) return;
    this.listening.add(t);
    es.addEventListener(t, this.onMessage as EventListener);
  }

  private headers(): Record<string, string> {
    const t = this.getToken();
    return t ? { Accept: "text/event-stream", Authorization: `Bearer ${t}` } : { Accept: "text/event-stream" };
  }

  private async ensure(): Promise<void> {
    if (this.es || this.starting) return;
    this.starting = true;
    try {
      if (this.cursor < 0) {
        const res = await fetch(`${this.baseUrl}/api/events/latest`, { headers: this.headers(), credentials: "same-origin" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        this.cursor = ((await res.json()) as { seq: number }).seq;
        for (const s of this.subs) if (s.queue === null && s.lastSeq === 0) s.lastSeq = this.cursor;
      }
      if (this.subs.size) this.connect();
    } catch {
      for (const s of this.subs) s.h.onError?.();
      this.timer = setTimeout(() => void this.ensure(), RETRY_MS);
    } finally {
      this.starting = false;
    }
  }

  private connect(): void {
    const q = new URLSearchParams({ after_seq: String(this.cursor) });
    const token = this.getToken();
    if (token) q.set("token", token); // fallback until cookie auth lands everywhere
    const es = new EventSource(`${this.baseUrl}/api/events?${q}`, { withCredentials: false });
    this.es = es;
    this.listening.clear();
    for (const t of this.types) this.listen(es, t);
    es.onopen = () => {
      this.open = true;
      for (const s of this.subs) s.h.onOpen?.();
    };
    es.onerror = () => {
      this.open = false;
      for (const s of this.subs) s.h.onError?.();
      if (es.readyState === EventSource.CLOSED) {
        es.close();
        if (this.es === es) this.es = null;
        this.timer = setTimeout(() => void this.ensure(), RETRY_MS);
      }
    };
  }

  private onMessage = (ev: MessageEvent<string>): void => {
    let e: ServerEvent;
    try {
      e = JSON.parse(ev.data) as ServerEvent;
    } catch {
      return;
    }
    if (typeof e.seq !== "number") return;
    if (e.seq > this.cursor) this.cursor = e.seq;
    for (const s of this.subs) {
      if (s.channel !== null && s.channel !== e.aggregate_id) continue;
      if (s.queue) s.queue.push(e);
      else this.deliver(s, e);
    }
  };

  private deliver(s: Sub, e: ServerEvent): void {
    if (e.seq <= s.lastSeq) return;
    s.lastSeq = e.seq;
    s.cb(e);
  }

  private async replay(s: Sub, retried: boolean): Promise<void> {
    const q = new URLSearchParams({ after_seq: String(s.lastSeq), follow: "false" });
    if (s.channel !== null) q.set("session_id", s.channel);
    let events: ServerEvent[] = [];
    try {
      const res = await fetch(`${this.baseUrl}/api/events?${q}`, { headers: this.headers(), credentials: "same-origin" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      events = parseSse(await res.text());
    } catch {
      if (!retried && this.subs.has(s)) {
        setTimeout(() => void this.replay(s, true), RETRY_MS);
        return;
      }
      s.h.onError?.();
    }
    if (!this.subs.has(s)) return;
    const queued = s.queue ?? [];
    s.queue = null;
    for (const e of events) this.deliver(s, e);
    for (const e of queued) this.deliver(s, e);
    s.h.onReplayed?.();
  }
}

export const EventsContext = createContext<EventHub | null>(null);

let fallback: EventHub | null = null;
/** The provider's hub, or a lazily created module hub (tests, components outside the provider). */
export function useEventHub(): EventHub {
  const hub = useContext(EventsContext);
  if (hub) return hub;
  fallback ??= createHub();
  return fallback;
}

export function createHub(): EventHub {
  const base = (import.meta.env.VITE_AUOREO_URL as string | undefined) ?? "";
  return new EventHub(base, getToken);
}

/**
 * Subscribe while mounted. `onEvent` may change every render (kept in a ref); the
 * subscription is re-created only when `channel`, `afterSeq` or `types` change.
 */
export function useEvents(
  opts: HubSubscription & { readonly enabled?: boolean },
  onEvent: (e: ServerEvent) => void,
  handlers: HubHandlers = {},
): void {
  const hub = useEventHub();
  const cb = useRef(onEvent);
  const hs = useRef(handlers);
  useEffect(() => {
    cb.current = onEvent;
    hs.current = handlers;
  });
  const types = (opts.types ?? []).join(",");
  const enabled = opts.enabled ?? true;
  useEffect(() => {
    if (!enabled) return;
    return hub.subscribe(
      { channel: opts.channel, afterSeq: opts.afterSeq, types: types ? types.split(",") : [] },
      (e) => cb.current(e),
      {
        onOpen: () => hs.current.onOpen?.(),
        onError: () => hs.current.onError?.(),
        onReplayed: () => hs.current.onReplayed?.(),
      },
    );
  }, [hub, opts.channel, opts.afterSeq, types, enabled]);
}
