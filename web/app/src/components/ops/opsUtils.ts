import type { ServerEvent } from "@sdk/client";
import { useCallback, useState } from "react";
import type { BadgeVariant } from "@/components/ui/badge";
import { EXTRA_EVENT_TYPES, useEvents } from "@/lib/eventsHub";
import { errorText } from "@/lib/utils";

/** Server-scoped event names the ops pages care about (not in the SDK's `EVENT_TYPES`). */
export const OPS_EVENT_TYPES = EXTRA_EVENT_TYPES.filter((t) => /^(plugin|cron|delivery)\./.test(t));

/**
 * Replay + follow the shared stream's `aggregate_id = "server"` channel (harness reloads,
 * plugin, cron and delivery events) and call `onEvent` for each event whose type matches
 * `prefixes`. `liveOnly` skips the history.
 */
export function useServerEvents(
  prefixes: readonly string[],
  onEvent: (e: ServerEvent) => void,
  liveOnly = false,
): void {
  const key = prefixes.join(",");
  useEvents({ channel: "server", afterSeq: liveOnly ? "live" : 0, types: OPS_EVENT_TYPES }, (e) => {
    if (key.split(",").some((p) => e.type.startsWith(p))) onEvent(e);
  });
}

/** Collect matching server events, newest first (bounded). */
export function useServerEventLog(prefixes: readonly string[], limit = 50): ServerEvent[] {
  const [log, setLog] = useState<ServerEvent[]>([]);
  useServerEvents(prefixes, (e) => setLog((l) => [e, ...l.filter((x) => x.seq !== e.seq)].slice(0, limit)));
  return log;
}

export interface ActionState {
  /** Key of the action in flight, if any (e.g. `run:morning_brief`). */
  busy: string | null;
  error: string | null;
  run: <T>(key: string, fn: () => Promise<T>) => Promise<T | undefined>;
  clearError: () => void;
}

/** One-at-a-time button actions with a shared busy key and error message. */
export function useAction(): ActionState {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const run = useCallback(async <T>(key: string, fn: () => Promise<T>): Promise<T | undefined> => {
    setBusy(key);
    setError(null);
    try {
      return await fn();
    } catch (e) {
      setError(errorText(e));
      return undefined;
    } finally {
      setBusy(null);
    }
  }, []);
  const clearError = useCallback(() => setError(null), []);
  return { busy, error, run, clearError };
}

/** Absolute local time plus a relative hint ("in 3h" / "5m ago"); "—" for null. */
export function fmtWhen(iso: string | null | undefined): string {
  if (!iso) return "—";
  const t = Date.parse(iso.endsWith("Z") || /[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`);
  if (Number.isNaN(t)) return iso;
  const s = Math.round((t - Date.now()) / 1000);
  const a = Math.abs(s);
  const span = a < 60 ? `${a}s` : a < 3600 ? `${Math.round(a / 60)}m` : a < 86400 ? `${Math.round(a / 3600)}h` : `${Math.round(a / 86400)}d`;
  const rel = a < 5 ? "now" : s > 0 ? `in ${span}` : `${span} ago`;
  return `${new Date(t).toLocaleString()} (${rel})`;
}

/** Badge colour for run / delivery / plugin / member / task status strings. */
export const OPS_STATUS: Record<string, BadgeVariant> = {
  ok: "success",
  delivered: "success",
  running: "info",
  delivering: "info",
  claimed: "info",
  lead: "secondary",
  pending: "secondary",
  queued: "secondary",
  ready: "secondary",
  done: "success",
  skipped: "outline",
  stopped: "outline",
  disabled: "outline",
  unknown: "warning",
  blocked: "warning",
  failed: "destructive",
  crashed: "destructive",
  invalid_config: "destructive",
};
