import { useVirtualizer } from "@tanstack/react-virtual";
import { ArrowDownIcon, NavigationIcon, PhoneIcon, SendIcon, SquareIcon } from "lucide-react";
import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router";
import { ApprovalCard } from "@/components/ApprovalCard";
import { ContextGauge } from "@/components/ContextGauge"; // --- G5b
import { INSERT_COMMAND_EVENT } from "@/components/CommandPalette";
import { ErrorNote } from "@/components/AppShell";
import { PlanPanel } from "@/components/PlanPanel";
import { PolicyChip } from "@/components/PolicyChip";
import { SessionActions } from "@/components/SessionActions";
import { SkillMention } from "@/components/SkillMention";
import { ThinkingToggle } from "@/components/ThinkingBlock";
import { ElicitationCard } from "@/components/ElicitationCard"; // --- I5
import { QuestionTabs } from "@/components/QuestionTabs"; // --- I5
import { ThreadItem } from "@/components/ThreadItem"; // --- I5
import { TokenBadge } from "@/components/TokenBadge"; // --- I5
import { VoiceButton } from "@/components/VoiceButton";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/input";
import { Tip } from "@/components/ui/tooltip";
import { useThread } from "@/hooks/useThread";
import { api } from "@/lib/api";
import { useControl } from "@/lib/controlContext";
import { compactSession, type OpNotice, RUN_COMPACT_EVENT } from "@/lib/sessionOps";
import { STATUS_VARIANT } from "@/lib/status";
import { useShowThinking } from "@/lib/thinking";
import { cn, errorText } from "@/lib/utils";

const CallPanel = lazy(() => import("@/components/call/CallPanel").then((m) => ({ default: m.CallPanel }))); // --- G5b

/** Remount per session id so all per-session state starts fresh. */
export function SessionRoute() {
  const { id = "" } = useParams();
  return <SessionPage key={id} id={id} />;
}

function SessionPage({ id }: { id: string }) {
  const t = useThread(id);
  const control = useControl();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [notice, setNoticeState] = useState<OpNotice | null>(null);
  const setNotice = (n: OpNotice | string) => setNoticeState(typeof n === "string" ? { level: "info", text: n } : n);
  const [archivedOverride, setArchivedOverride] = useState<boolean | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const didInitialScroll = useRef(false);
  const [atEnd, setAtEnd] = useState(true);
  const [callOpen, setCallOpen] = useState(false); // --- G5b
  const [showThinking, setShowThinking] = useShowThinking(); // --- G7: per-user thinking toggle

  const items = showThinking ? t.items : t.items.filter((it) => it.kind !== "reasoning");
  const pendingPerms = Object.values(t.state.permissions);
  const pendingQs = Object.values(t.state.questions);
  const waitingTools = new Set(pendingPerms.map((p) => p.tool));
  const running = t.state.running || t.session?.status === "running";
  const title = t.state.title || t.session?.title || "";
  const archived = archivedOverride ?? t.state.archived ?? t.session?.archived ?? false;

  // eslint-disable-next-line react-hooks/incompatible-library -- TanStack Virtual returns unmemoizable fns; no React Compiler here
  const virtualizer = useVirtualizer({
    count: items.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => 64,
    getItemKey: (i) => items[i]?.key ?? i,
    anchorTo: "end",
    followOnAppend: true,
    scrollEndThreshold: 80,
    overscan: 8,
  });

  useEffect(() => {
    if (!didInitialScroll.current && items.length > 0) {
      didInitialScroll.current = true;
      requestAnimationFrame(() => virtualizer.scrollToIndex(items.length - 1, { align: "end" }));
    }
  }, [items.length, virtualizer]);

  useEffect(() => {
    const onInsert = (e: Event) => {
      const detail = (e as CustomEvent<string>).detail;
      setText(detail);
      inputRef.current?.focus();
    };
    const onCompact = () => void compactSession(id).then(setNotice, (e: unknown) => setErr(errorText(e)));
    window.addEventListener(INSERT_COMMAND_EVENT, onInsert);
    window.addEventListener(RUN_COMPACT_EVENT, onCompact);
    return () => {
      window.removeEventListener(INSERT_COMMAND_EVENT, onInsert);
      window.removeEventListener(RUN_COMPACT_EVENT, onCompact);
    };
  }, [id]);

  const guard = async (fn: () => Promise<unknown>) => {
    setErr(null);
    try {
      await fn();
    } catch (e) {
      setErr(errorText(e));
    }
  };

  const send = async () => {
    const message = text.trim();
    if (!message || busy) return;
    setBusy(true);
    await guard(async () => {
      // `/compact` runs directly so "nothing to compact" (422) comes back as a notice.
      const verb = /^\/(steer|queue)\s+([\s\S]+)$/.exec(message); // --- G5b palette verbs
      if (/^\/compact\s*$/.test(message)) setNotice(await compactSession(id));
      else if (verb?.[1] === "steer") setNotice(`Steer sent (${await control.steer(id, verb[2] ?? "")})`);
      else if (verb) await api.chat({ session_id: id, message: verb[2] ?? "", mode: "followup" });
      else await api.chat({ session_id: id, message });
      setText("");
    });
    setBusy(false);
    requestAnimationFrame(() => virtualizer.scrollToIndex(items.length, { align: "end" }));
  };

  const steer = () =>
    guard(async () => {
      const message = text.trim();
      if (!message) return;
      const via = await control.steer(id, message);
      setText("");
      setNotice(`Steer sent (${via})`);
    });

  const interrupt = () =>
    guard(async () => {
      const via = await control.interrupt(id);
      setNotice(`Interrupt sent (${via})`);
    });

  return (
    <div className="flex h-full flex-col">
      <header className="flex flex-wrap items-center gap-2 border-b px-4 py-2">
        <div className="min-w-0 flex-1">
          <h1 className="truncate text-base font-semibold" data-testid="session-title">
            {title || "Untitled session"}
            {archived && (
              <Badge variant="outline" className="ml-2 align-middle" data-testid="archived-badge">
                archived
              </Badge>
            )}
          </h1>
          <div className="flex flex-wrap items-center gap-1.5 text-xs">
            <Link to="/sessions" className="text-muted-foreground hover:underline">
              Sessions
            </Link>
            <span className="text-muted-foreground">/</span>
            <span className="font-mono text-muted-foreground">{id}</span>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-1.5" aria-label="Session details">
          <Badge variant="outline" title="Agent">
            {t.state.agent ?? t.session?.agent ?? "…"}
          </Badge>
          {(t.state.model ?? t.session?.model) && (
            <Badge variant="outline" title="Model" className="font-mono">
              {t.state.model ?? t.session?.model}
            </Badge>
          )}
          <PolicyChip choice={t.state.policy} />
          <Badge variant={running ? "info" : (STATUS_VARIANT[t.session?.status ?? ""] ?? "secondary")} data-testid="session-status">
            {running ? "running" : (t.session?.status ?? "…")}
          </Badge>
          <TokenBadge sessionId={id} usage={t.state.usage} running={running} />
          <ContextGauge sessionId={id} refreshKey={`${running}:${t.state.usage.prompt}:${items.length}`} />
          {!t.streamOk && <Badge variant="warning">stream reconnecting</Badge>}
          <ThinkingToggle on={showThinking} onChange={setShowThinking} />
          <Button
            size="sm"
            variant={callOpen ? "secondary" : "outline"}
            aria-pressed={callOpen}
            onClick={() => setCallOpen((o) => !o)}
            data-testid="call-toggle"
          >
            <PhoneIcon aria-hidden /> Call
          </Button>
        </div>
        <SessionActions
          id={id}
          title={title}
          archived={archived}
          running={running}
          onArchived={setArchivedOverride}
          onNotice={setNotice}
        />
      </header>
      {callOpen && (
        <Suspense>
          <CallPanel sessionId={id} />
        </Suspense>
      )}

      <div className="relative min-h-0 flex-1">
        <div
          ref={scrollRef}
          className="h-full overflow-y-auto px-4"
          role="log"
          aria-label="Conversation"
          aria-live="polite"
          data-testid="thread"
          onScroll={() => setAtEnd(virtualizer.isAtEnd())}
        >
          {items.length === 0 && (
            <p className="py-10 text-center text-sm text-muted-foreground">
              {t.live ? "No messages yet. Say hello below." : "Loading…"}
            </p>
          )}
          <div className="relative mx-auto w-full max-w-4xl" style={{ height: virtualizer.getTotalSize() }}>
            {virtualizer.getVirtualItems().map((v) => {
              const item = items[v.index];
              if (!item) return null;
              return (
                <div
                  key={v.key}
                  data-index={v.index}
                  ref={virtualizer.measureElement}
                  className="absolute left-0 w-full py-1.5"
                  style={{ transform: `translateY(${v.start}px)` }}
                >
                  <ThreadItem item={item} awaiting={item.kind === "tool" && item.status === "running" && waitingTools.has(item.name)} />
                </div>
              );
            })}
          </div>
        </div>
        {!atEnd && items.length > 0 && (
          <Button
            size="sm"
            variant="secondary"
            className="absolute right-6 bottom-3 shadow"
            onClick={() => virtualizer.scrollToIndex(items.length - 1, { align: "end" })}
          >
            <ArrowDownIcon aria-hidden /> Latest
          </Button>
        )}
      </div>

      <footer className="space-y-2 border-t px-4 py-3">
        <div className="mx-auto w-full max-w-4xl space-y-2">
          <ErrorNote error={t.error ?? err} />
          <PlanPanel sessionId={id} plan={t.state.plan} planMode={t.state.planMode} />
          {pendingPerms.map((p) => (
            <ApprovalCard key={p.request_id} request={p} onDone={() => t.dismissPermission(p.request_id)} />
          ))}
          {Object.entries(t.state.elicitations).map(([eid, e]) => (
            <ElicitationCard key={eid} data={e} onDone={() => t.dismissElicitation(eid)} />
          ))}
          <QuestionTabs pending={pendingQs} onDone={t.dismissQuestion} />
          <SkillMention text={text} onPick={(next) => {
              setText(next);
              inputRef.current?.focus();
            }} />
          <form
            className="flex items-end gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              void send();
            }}
          >
            <Textarea
              ref={inputRef}
              aria-label="Message"
              data-testid="composer"
              rows={2}
              placeholder={running ? "Queue a follow-up, or steer the running turn…" : "Message the agent — Enter to send, Shift+Enter for a newline"}
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  void send();
                }
              }}
            />
            <div className="flex flex-col gap-1">
              <Button type="submit" disabled={busy || !text.trim()} aria-label="Send message">
                <SendIcon aria-hidden /> Send
              </Button>
              <VoiceButton sessionId={id} items={items} />
              {running && (
                <div className="flex gap-1">
                  <Tip label="Steer the running turn with this text">
                    <Button variant="outline" size="icon-sm" aria-label="Steer" disabled={!text.trim()} onClick={() => void steer()}>
                      <NavigationIcon />
                    </Button>
                  </Tip>
                  <Tip label="Interrupt the running turn">
                    <Button variant="outline" size="icon-sm" aria-label="Interrupt" onClick={() => void interrupt()}>
                      <SquareIcon />
                    </Button>
                  </Tip>
                </div>
              )}
            </div>
          </form>
          {notice && (
            <p
              role="status"
              data-testid="action-notice"
              data-level={notice.level}
              className={cn(
                "text-xs",
                notice.level === "info" && "text-muted-foreground",
                notice.level === "warn" && "text-warning",
                notice.level === "error" && "text-destructive",
              )}
            >
              {notice.text}
            </p>
          )}
        </div>
      </footer>
    </div>
  );
}
