import { ExternalLinkIcon } from "lucide-react";
import { Link } from "react-router";
import { ThreadItem } from "@/components/ThreadItem";
import { TokenBadge } from "@/components/TokenBadge";
import { useThread } from "@/hooks/useThread";

/**
 * I5: a run's session transcript inline (Jobs run drawer, Teams member view) — the same
 * thread cards and token badge as the Session page, read-only (approvals and questions are
 * answered on the Session page, linked here).
 */
export function RunTranscript({ sessionId, title }: { sessionId: string; title?: string }) {
  const t = useThread(sessionId);
  const running = t.state.running || t.session?.status === "running";
  const pending = Object.keys(t.state.permissions).length + Object.keys(t.state.questions).length;
  return (
    <section className="space-y-2 rounded-lg border p-2" aria-label={`Transcript of ${sessionId}`} data-testid="run-transcript" data-session={sessionId}>
      <header className="flex flex-wrap items-center gap-2 text-xs">
        <span className="min-w-0 flex-1 truncate font-medium">{title ?? (t.state.title || t.session?.title || sessionId)}</span>
        <TokenBadge sessionId={sessionId} usage={t.state.usage} running={running} />
        <Link to={`/session/${sessionId}`} className="inline-flex items-center gap-1 text-primary hover:underline">
          Open session <ExternalLinkIcon className="size-3" aria-hidden />
        </Link>
      </header>
      {pending > 0 && (
        <p className="text-xs text-warning" role="status">
          {pending} approval/question{pending === 1 ? "" : "s"} waiting — answer on the Session page.
        </p>
      )}
      <div className="max-h-96 space-y-2 overflow-y-auto pr-1" role="log" aria-label="Run transcript">
        {t.items.length === 0 && <p className="py-4 text-center text-xs text-muted-foreground">{t.live ? "No messages." : "Loading…"}</p>}
        {t.items.map((it) => (
          <ThreadItem key={it.key} item={it} readOnly />
        ))}
      </div>
    </section>
  );
}
