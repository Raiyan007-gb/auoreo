import type { PendingPermission } from "@sdk/client";
import { useCallback, useEffect, useState, useSyncExternalStore } from "react";
import { approvalsStore } from "@/lib/approvalsStore";
import { useControl } from "@/lib/controlContext";

const POLL_MS = 3000;

export interface ApprovalsView {
  items: readonly PendingPermission[];
  error: unknown;
  /** Force a re-fetch (the inbox refresh button). */
  refresh: () => void;
  /** Drop a request answered from this tab before its `permission.replied` event lands. */
  remove: (requestId: string) => void;
}

/**
 * Pending approvals, shared: seeded through `approvalsStore` once the control channel is up
 * (or known to be unavailable), re-seeded per connection, kept live by `permission.requested`
 * / `permission.replied`, and polled over REST only while the WebSocket is down.
 */
export function useApprovals(): ApprovalsView {
  const control = useControl();
  const ready = Boolean(control.connected) || control.state === "unavailable";
  const wsUp = control.state === "open";
  const items = useSyncExternalStore(approvalsStore.subscribe, approvalsStore.getSnapshot);
  const [error, setError] = useState<unknown>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (!ready) return;
    let cancelled = false;
    const key = control.connected ?? "unavailable";
    const load = (force: boolean) =>
      approvalsStore
        .ensure(key, force)
        .then(() => !cancelled && setError(null))
        .catch((e: unknown) => !cancelled && setError(e));
    void load(tick > 0);
    const stop = wsUp ? null : approvalsStore.poll(POLL_MS, () => void load(true));
    return () => {
      cancelled = true;
      stop?.();
    };
  }, [ready, control.connected, wsUp, tick]);

  useEffect(
    () =>
      control.listen((e) => {
        if (e.type === "permission.requested") {
          const d = e.data as unknown as Omit<PendingPermission, "session_id">;
          approvalsStore.add({ ...d, session_id: e.aggregate_id });
        } else if (e.type === "permission.replied") {
          approvalsStore.remove(String(e.data.request_id ?? ""));
        }
      }),
    [control],
  );

  const refresh = useCallback(() => setTick((t) => t + 1), []);
  return { items, error, refresh, remove: approvalsStore.remove };
}
