import type { ExportFormat } from "@sdk/contracts";
import { useVirtualizer } from "@tanstack/react-virtual";
import { ArrowDownIcon, DownloadIcon, MinimizeIcon, NavigationIcon, SendIcon, SquareIcon } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router";
import { ApprovalCard } from "@/components/ApprovalCard";
import { INSERT_COMMAND_EVENT } from "@/components/CommandPalette";
import { ErrorNote } from "@/components/AppShell";
import { QuestionForm } from "@/components/QuestionForm";
import { ToolCard } from "@/components/ToolCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/input";
import { Tip } from "@/components/ui/tooltip";
import { useSupports } from "@/hooks/useAsync";
import { useThread } from "@/hooks/useThread";
import { api } from "@/lib/api";
import { useControl } from "@/lib/controlContext";
import { STATUS_VARIANT } from "@/lib/status";
import type { Item } from "@/lib/thread";
import { cn, download, errorText, fmtTokens, fmtUsd } from "@/lib/utils";

function ItemView({ item, awaiting }: { item: Item; awaiting: boolean }) {
  switch (item.kind) {
    case "user":
      return (
        <div className="flex justify-end" data-testid="msg-user">
          <div className="max-w-[80%] rounded-lg bg-secondary px-3 py-2 whitespace-pre-wrap">
            {item.steer && <span className="mr-1 text-xs text-muted-foreground">steer ·</span>}
            {item.text}
          </div>
        </div>
      );
    case "assistant":
      return (
        <div className="max-w-[90%] leading-relaxed whitespace-pre-wrap" data-testid="msg-assistant" data-streaming={item.streaming}>
          {item.text}
          {item.streaming && <span aria-hidden className="ml-0.5 inline-block h-4 w-1.5 animate-pulse bg-foreground align-text-bottom" />}
        </div>
      );
    case "tool":
      return <ToolCard tool={item} awaitingApproval={awaiting} />;
    case "notice":
      return (
        <div
          role="status"
          className={cn(
            "rounded-md px-3 py-1.5 text-xs",
            item.level === "error" && "bg-destructive/10 text-destructive",
            item.level === "warn" && "bg-warning/15 text-warning",
            item.level === "info" && "bg-muted text-muted-foreground",
          )}
        >
          {item.text}
        </div>
      );
  }
}

/** Remount per session id so all per-session state starts fresh. */
export function SessionRoute() {
  const { id = "" } = useParams();
  return <SessionPage key={id} id={id} />;
}

function SessionPage({ id }: { id: string }) {
  const t = useThread(id);
  const control = useControl();
  const canCompact = useSupports("compact");
  const canExport = useSupports("export");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const didInitialScroll = useRef(false);
  const [atEnd, setAtEnd] = useState(true);

  const items = t.items;
  const pendingPerms = Object.values(t.state.permissions);
  const pendingQs = Object.values(t.state.questions);
  const waitingTools = new Set(pendingPerms.map((p) => p.tool));
  const running = t.state.running || t.session?.status === "running";

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
    window.addEventListener(INSERT_COMMAND_EVENT, onInsert);
    return () => window.removeEventListener(INSERT_COMMAND_EVENT, onInsert);
  }, []);

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
      await api.chat({ session_id: id, message });
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

  const compact = () =>
    guard(async () => {
      const r = await api.compact(id);
      setNotice(`Compacted ${fmtTokens(r.pre_tokens)} → ${fmtTokens(r.post_tokens)} tokens`);
    });

  const exportAs = (format: ExportFormat) =>
    guard(async () => {
      const res = await api.exportSession(id, format);
      download(`${id}.${format}`, await res.blob());
    });

  const totalTokens = t.state.usage.prompt + t.state.usage.completion;

  return (
    <div className="flex h-full flex-col">
      <header className="flex flex-wrap items-center gap-2 border-b px-4 py-2">
        <div className="min-w-0 flex-1">
          <h1 className="truncate text-base font-semibold" data-testid="session-title">
            {t.session?.title || "Untitled session"}
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
          <Badge variant={running ? "info" : (STATUS_VARIANT[t.session?.status ?? ""] ?? "secondary")} data-testid="session-status">
            {running ? "running" : (t.session?.status ?? "…")}
          </Badge>
          <Tip
            label={`${t.state.usage.prompt.toLocaleString()} prompt + ${t.state.usage.completion.toLocaleString()} completion · ${fmtUsd(t.state.usage.cost)}`}
          >
            <Badge variant="secondary" data-testid="token-badge" tabIndex={0}>
              {fmtTokens(totalTokens)} tok · {fmtUsd(t.state.usage.cost)}
            </Badge>
          </Tip>
          {!t.streamOk && <Badge variant="warning">stream reconnecting</Badge>}
        </div>
        <div className="flex items-center gap-1">
          {canCompact && (
            <Button variant="outline" size="sm" onClick={() => void compact()} disabled={running}>
              <MinimizeIcon aria-hidden /> Compact
            </Button>
          )}
          {canExport && (
            <>
              <Button variant="outline" size="sm" onClick={() => void exportAs("md")} aria-label="Export as Markdown">
                <DownloadIcon aria-hidden /> MD
              </Button>
              <Button variant="outline" size="sm" onClick={() => void exportAs("json")} aria-label="Export as JSON">
                <DownloadIcon aria-hidden /> JSON
              </Button>
            </>
          )}
        </div>
      </header>

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
                  <ItemView item={item} awaiting={item.kind === "tool" && item.status === "running" && waitingTools.has(item.name)} />
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
          {pendingPerms.map((p) => (
            <ApprovalCard key={p.request_id} request={p} onDone={() => t.dismissPermission(p.request_id)} />
          ))}
          {pendingQs.map((q) => (
            <QuestionForm key={q.request_id} request={q} onDone={() => t.dismissQuestion(q.request_id)} />
          ))}
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
            <p role="status" className="text-xs text-muted-foreground">
              {notice}
            </p>
          )}
        </div>
      </footer>
    </div>
  );
}
