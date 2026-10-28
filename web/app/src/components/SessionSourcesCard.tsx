import type { SessionInfo, SessionSources } from "@sdk/client";
import { useEffect, useState } from "react";
import { ErrorNote } from "@/components/AppShell";
import { Badge } from "@/components/ui/badge";
import { Card, CardDescription, CardTitle } from "@/components/ui/card";
import { NativeSelect } from "@/components/ui/input";
import { useAsync } from "@/hooks/useAsync";
import { api } from "@/lib/api";
import { errorText } from "@/lib/utils";

/**
 * Per-session enable/disable of MCP servers and plugins (`PUT /api/sessions/{id}/sources`).
 * A disabled source's tools and skills are left out of that session's turns from the next
 * turn on; other sessions are unaffected.
 */
export function SessionSourcesCard({ initialSession }: { initialSession?: string }) {
  const sessions = useAsync<SessionInfo[]>(() => api.listSessions(100), []);
  const [sid, setSid] = useState(initialSession ?? "");
  const [data, setData] = useState<SessionSources | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const chosen = sid || sessions.data?.[0]?.id || "";

  useEffect(() => {
    if (!chosen) return;
    let cancelled = false;
    api
      .sessionSources(chosen)
      .then((d) => !cancelled && (setData(d), setErr(null)))
      .catch((e: unknown) => !cancelled && setErr(errorText(e)));
    return () => {
      cancelled = true;
    };
  }, [chosen]);

  const toggle = async (id: string, enabled: boolean) => {
    if (!data) return;
    const disabled = enabled ? data.disabled.filter((x) => x !== id) : [...data.disabled, id];
    const before = data;
    // Optimistic: flip the row now, reconcile with the server's answer (or roll back on error).
    setData({ ...data, disabled, sources: data.sources.map((x) => (x.id === id ? { ...x, enabled } : x)) });
    setBusy(true);
    try {
      setData(await api.setSessionSources(chosen, disabled));
      setErr(null);
    } catch (e) {
      setData(before);
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card className="gap-2" data-testid="session-sources">
      <CardTitle className="text-sm">Per-session sources</CardTitle>
      <CardDescription>Switch MCP servers and plugins off for one session; its next turn drops their tools and skills.</CardDescription>
      <NativeSelect aria-label="Session" value={chosen} onChange={(e) => setSid(e.target.value)} data-testid="session-sources-select">
        {(sessions.data ?? []).map((s) => (
          <option key={s.id} value={s.id}>
            {s.title || s.id}
          </option>
        ))}
      </NativeSelect>
      <ErrorNote error={err ?? sessions.error} />
      {data && data.sources.length === 0 && <p className="text-xs text-muted-foreground">No MCP servers or plugins defined.</p>}
      <ul className="max-h-64 space-y-1 overflow-auto text-xs">
        {data?.sources.map((s) => (
          <li
            key={s.id}
            className="flex items-center gap-2"
            data-testid="session-source"
            data-id={s.id}
            data-enabled={s.enabled}
            title={s.status === "disabled" ? "Off for every session right now; this choice applies once it is switched on" : undefined}
          >
            <input
              type="checkbox"
              id={`src-${s.id}`}
              checked={s.enabled}
              disabled={busy}
              onChange={(e) => void toggle(s.id, e.target.checked)}
              aria-label={`Enable ${s.id} for this session`}
            />
            <label htmlFor={`src-${s.id}`} className="min-w-0 flex-1 truncate font-mono">
              {s.id}
            </label>
            <Badge variant="outline">{s.status === "disabled" ? "off everywhere" : s.status}</Badge>
            <span className="text-muted-foreground tabular-nums" title={(s.skills ?? []).join(", ")}>
              {s.tools} tools{(s.skills ?? []).length ? ` · ${(s.skills ?? []).length} skills` : ""}
            </span>
          </li>
        ))}
      </ul>
    </Card>
  );
}
