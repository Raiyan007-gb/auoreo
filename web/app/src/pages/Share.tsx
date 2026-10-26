import type { MessageOut, SharedSession } from "@sdk/client";
import { EyeIcon } from "lucide-react";
import { useEffect, useState } from "react";
import { useParams } from "react-router";
import { ThreadItem } from "@/components/ThreadItem";
import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";
import { fromMessages } from "@/lib/thread";
import { errorText, relTime } from "@/lib/utils";

/** List content (text + image parts) as text, like the server's `/messages` flattening. */
function text(content: unknown): string | null {
  if (content == null || typeof content === "string") return content ?? null;
  if (!Array.isArray(content)) return JSON.stringify(content);
  return content
    .map((p: { type?: string; text?: string }) => (p?.type === "text" ? (p.text ?? "") : p?.type === "image_url" ? "[image]" : ""))
    .join("\n");
}

function toMessages(raw: SharedSession["messages"]): MessageOut[] {
  return raw.map((m, idx) => ({
    idx,
    role: String(m.role ?? ""),
    content: text(m.content),
    tool_calls: (m.tool_calls as MessageOut["tool_calls"]) ?? null,
    tool_call_id: (m.tool_call_id as string | undefined) ?? null,
    name: (m.name as string | undefined) ?? null,
  }));
}

/**
 * I5: `/share/:token` — the public, read-only transcript behind a share link. No auth (the
 * token is the capability), no composer, approvals or session controls; secrets were
 * redacted by the server. Rendered outside the app shell and its live connections.
 */
export function SharePage() {
  const { token = "" } = useParams();
  const [data, setData] = useState<SharedSession | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    api
      .sharedSession(token)
      .then((d) => !cancelled && setData(d))
      .catch((e: unknown) => !cancelled && setErr(errorText(e)));
    return () => {
      cancelled = true;
    };
  }, [token]);

  const items = data ? fromMessages(toMessages(data.messages)) : [];
  return (
    <div className="min-h-svh bg-background text-foreground" data-testid="share-page">
      <header className="border-b px-4 py-3">
        <div className="mx-auto flex max-w-4xl flex-wrap items-center gap-2">
          <h1 className="min-w-0 flex-1 truncate text-base font-semibold" data-testid="share-title">
            {data ? data.session.title || "Untitled session" : err ? "Share link unavailable" : "Loading…"}
          </h1>
          <Badge variant="outline" data-testid="share-readonly">
            <EyeIcon aria-hidden className="size-3" /> Read-only
          </Badge>
          {data && (
            <span className="text-xs text-muted-foreground" title={data.session.updated_at}>
              {data.session.agent} · updated {relTime(data.session.updated_at)}
            </span>
          )}
        </div>
      </header>
      <main className="mx-auto max-w-4xl space-y-3 px-4 py-4" role="log" aria-label="Shared conversation">
        {err && (
          <p role="alert" className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive" data-testid="share-error">
            {err.includes("not found") ? "This link was revoked or never existed." : err}
          </p>
        )}
        {data && items.length === 0 && <p className="text-sm text-muted-foreground">This session has no messages yet.</p>}
        {items.map((it) => (
          <ThreadItem key={it.key} item={it} readOnly />
        ))}
        {data && (
          <p className="pt-4 text-center text-xs text-muted-foreground">
            Shared from AUOREO · secrets are redacted · this page cannot change the session
          </p>
        )}
      </main>
    </div>
  );
}
