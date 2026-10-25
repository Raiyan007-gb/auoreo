import type { SessionContext } from "@sdk/client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

/** Segments of the bar, in request order. */
export function contextParts(c: SessionContext) {
  const w = Math.max(1, c.context_window);
  return [
    { key: "system", label: "System prompt", tokens: c.system_tokens, pct: (c.system_tokens / w) * 100, cls: "bg-info" },
    { key: "tools", label: "Tool schemas", tokens: c.tools_tokens, pct: (c.tools_tokens / w) * 100, cls: "bg-primary/70" },
    { key: "messages", label: "Messages", tokens: c.messages_tokens, pct: (c.messages_tokens / w) * 100, cls: "bg-success" },
  ];
}

/** 0 = fine, 1 = past auto-compaction, 2 = ≥ 90 % (nudge to /usage). */
export function pressureLevel(c: SessionContext): 0 | 1 | 2 {
  if (c.ratio >= 0.9) return 2;
  return c.pressure + c.tools_tokens >= c.auto_compact_at ? 1 : 0;
}

export function useSessionContext(sessionId: string, refreshKey: unknown): SessionContext | null {
  const [ctx, setCtx] = useState<SessionContext | null>(null);
  useEffect(() => {
    let cancelled = false;
    api
      .sessionContext(sessionId)
      .then((c) => !cancelled && setCtx(c))
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [sessionId, refreshKey]);
  return ctx;
}

