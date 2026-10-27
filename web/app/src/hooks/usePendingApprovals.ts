import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useControl } from "@/lib/controlContext";

/**
 * Count of permission requests waiting on a person, for the sidebar badge: seeded from
 * `GET /api/permissions/pending` (and each WS `connected` frame), then kept live by
 * `permission.requested` / `permission.replied` events.
 */
export function usePendingApprovals(): number {
  const control = useControl();
  const [ids, setIds] = useState<ReadonlySet<string>>(new Set());

  useEffect(() => {
    let cancelled = false;
    api
      .pendingPermissions()
      .then((p) => !cancelled && setIds(new Set(p.map((x) => x.request_id))))
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [control.connected]);

  useEffect(
    () =>
      control.listen((e) => {
        const rid = String(e.data.request_id ?? "");
        if (!rid) return;
        if (e.type === "permission.requested") setIds((s) => new Set(s).add(rid));
        else if (e.type === "permission.replied")
          setIds((s) => {
            const n = new Set(s);
            n.delete(rid);
            return n;
          });
      }),
    [control],
  );

  return ids.size;
}
