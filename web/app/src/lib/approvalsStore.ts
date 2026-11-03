import type { PendingPermission } from "@sdk/client";
import { api } from "@/lib/api";

type Listener = () => void;

let items: readonly PendingPermission[] = [];
const listeners = new Set<Listener>();
let inflight: Promise<readonly PendingPermission[]> | null = null;
let seeded = false;
let seededFor: unknown = undefined;
let pollTimer: ReturnType<typeof setInterval> | null = null;
let pollers = 0;

function emit(): void {
  for (const l of listeners) l();
}

/**
 * The one approvals store (ui-ux plan, item 10): every consumer of pending permissions (the
 * sidebar badge, the inbox, a session thread) reads this snapshot, so `GET /api/permissions/pending`
 * is fetched once per control connection instead of once per component. Live `permission.*`
 * events update it in place; a 3 s REST poll (one timer, ref-counted) runs only while the
 * WebSocket is down.
 */
export const approvalsStore = {
  subscribe(l: Listener): () => void {
    listeners.add(l);
    return () => {
      listeners.delete(l);
    };
  },
  getSnapshot(): readonly PendingPermission[] {
    return items;
  },
  add(p: PendingPermission): void {
    if (items.some((x) => x.request_id === p.request_id)) return;
    items = [...items, p];
    emit();
  },
  remove(requestId: string): void {
    if (!items.some((x) => x.request_id === requestId)) return;
    items = items.filter((x) => x.request_id !== requestId);
    emit();
  },
  /** Seed once per `key` (the control `connected` frame); concurrent callers share the request. */
  ensure(key: unknown, force = false): Promise<readonly PendingPermission[]> {
    if (inflight && !force) return inflight;
    if (!force && seeded && key === seededFor) return Promise.resolve(items);
    seeded = true;
    seededFor = key;
    const p: Promise<readonly PendingPermission[]> = api
      .pendingPermissions()
      .then((rows) => {
        if (inflight === p) {
          items = rows;
          emit();
        }
        return items;
      })
      .finally(() => {
        if (inflight === p) inflight = null;
      });
    inflight = p;
    return p;
  },
  /** The seeded list (waiting for a seed in flight), fetching only if nothing seeded it yet. */
  current(): Promise<readonly PendingPermission[]> {
    if (!seeded) return approvalsStore.ensure("initial");
    return inflight ?? Promise.resolve(items);
  },
  /** One shared interval while any consumer asks for polling; returns the stop function. */
  poll(ms: number, fn: () => void): () => void {
    pollers += 1;
    pollTimer ??= setInterval(fn, ms);
    return () => {
      pollers -= 1;
      if (pollers <= 0 && pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
        pollers = 0;
      }
    };
  },
};
