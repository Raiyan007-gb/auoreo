import type { CommandInfo, HarnessEntry, SessionInfo } from "@sdk/client";
import { Command } from "cmdk";
import { Dialog as DialogPrimitive } from "radix-ui";
import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router";
import { api } from "@/lib/api";
import { RUN_COMPACT_EVENT } from "@/lib/sessionOps";
import { NAV } from "@/components/nav";
import { RegistryGroups } from "@/components/PaletteGroups";
import { bumpFreq, byFreq, readFreq, SESSION_VERBS, useRegistries } from "@/lib/palette";

export const INSERT_COMMAND_EVENT = "auoreo:insert-command";

const itemCls =
  "flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-sm aria-selected:bg-accent aria-selected:text-accent-foreground";
const groupCls =
  "[&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1 [&_[cmdk-group-heading]]:text-xs [&_[cmdk-group-heading]]:text-muted-foreground";

/**
 * Cmd/Ctrl-K: slash commands (`GET /api/commands`) + session verbs (`/steer /queue /fork
 * /resume`), pages, sessions, agents (new session), models, skills, plugins and MCP servers.
 * Commands are ordered freq-first (selection counts per browser).
 */
export function CommandPalette({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const navigate = useNavigate();
  const location = useLocation();
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [agents, setAgents] = useState<HarnessEntry[]>([]);
  const [commands, setCommands] = useState<CommandInfo[]>([]);
  const onSession = location.pathname.startsWith("/session/");
  const sessionId = onSession ? decodeURIComponent(location.pathname.slice("/session/".length)) : null;
  const [search, setSearch] = useState("");
  const reg = useRegistries(open);
  const freq = open ? readFreq() : {};

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    api.listSessions(50).then((s) => !cancelled && setSessions(s)).catch(() => undefined);
    api.harnessKind("agents").then((a) => !cancelled && setAgents(a)).catch(() => undefined);
    api.commands().then((c) => !cancelled && setCommands(c)).catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [open]);

  const run = (fn: () => void) => {
    onOpenChange(false);
    setSearch("");
    fn();
  };
  const runKey = (key: string, fn: () => void) => {
    bumpFreq(key);
    run(fn);
  };
  const insert = (text: string) => window.dispatchEvent(new CustomEvent(INSERT_COMMAND_EVENT, { detail: text }));
  const verb = (name: string) => {
    if (name === "resume") {
      bumpFreq("verb:resume");
      setSearch("session ");
      return;
    }
    runKey(`verb:${name}`, () => {
      if (name === "fork" && sessionId) void api.fork(sessionId).then((f) => navigate(`/session/${f.id}`));
      else insert(`/${name} `);
    });
  };
  const verbs = SESSION_VERBS.filter((v) => onSession || v.name === "resume");

  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-black/40" />
        <DialogPrimitive.Content
          className="fixed top-[15%] left-1/2 z-50 w-full max-w-[calc(100%-2rem)] -translate-x-1/2 overflow-hidden rounded-lg border bg-popover text-popover-foreground shadow-lg sm:max-w-xl"
          aria-describedby={undefined}
        >
          <DialogPrimitive.Title className="sr-only">Command palette</DialogPrimitive.Title>
          <Command label="Command palette" className="flex flex-col">
            <Command.Input
              autoFocus
              value={search}
              onValueChange={setSearch}
              placeholder="Jump to a page, session, agent or command…"
              className="h-10 border-b bg-transparent px-3 text-sm outline-none placeholder:text-muted-foreground"
            />
            <Command.List className="max-h-80 overflow-y-auto p-1">
              <Command.Empty className="py-6 text-center text-sm text-muted-foreground">No results.</Command.Empty>
              {onSession && commands.length > 0 && (
                <Command.Group heading="Commands" className={groupCls}>
                  {byFreq(commands, (c) => `cmd:${c.name}`, freq).map((c) => (
                    <Command.Item
                      key={c.name}
                      data-testid="palette-command"
                      data-command={c.name}
                      value={`/${c.name} ${c.description}`}
                      className={itemCls}
                      onSelect={() => runKey(`cmd:${c.name}`, () => insert(`/${c.name} `))}
                    >
                      <span className="font-mono">/{c.name}</span>
                      {c.args_hint && <span className="font-mono text-xs text-muted-foreground">{c.args_hint}</span>}
                      <span className="ml-auto truncate text-xs text-muted-foreground">{c.description}</span>
                    </Command.Item>
                  ))}
                  {commands.some((c) => c.handler === "compact") && (
                    <Command.Item
                      value="run compact now session context"
                      className={itemCls}
                      onSelect={() => run(() => window.dispatchEvent(new CustomEvent(RUN_COMPACT_EVENT)))}
                    >
                      <span>Run</span>
                      <span className="font-mono">/compact</span>
                      <span>now</span>
                      <span className="ml-auto truncate text-xs text-muted-foreground">compact this session's context</span>
                    </Command.Item>
                  )}
                </Command.Group>
              )}
              {verbs.length > 0 && (
                <Command.Group heading="Session" className={groupCls}>
                  {byFreq(verbs, (v) => `verb:${v.name}`, freq).map((v) => (
                    <Command.Item
                      key={v.name}
                      data-testid="palette-verb"
                      data-command={v.name}
                      value={`/${v.name} ${v.description}`}
                      className={itemCls}
                      onSelect={() => verb(v.name)}
                    >
                      <span className="font-mono">/{v.name}</span>
                      {v.hint && <span className="font-mono text-xs text-muted-foreground">{v.hint}</span>}
                      <span className="ml-auto truncate text-xs text-muted-foreground">{v.description}</span>
                    </Command.Item>
                  ))}
                </Command.Group>
              )}
              <Command.Group heading="Pages" className={groupCls}>
                {NAV.map((n) => (
                  <Command.Item key={n.to} value={`page ${n.label}`} className={itemCls} onSelect={() => run(() => void navigate(n.to))}>
                    <n.icon className="size-4" aria-hidden />
                    {n.label}
                  </Command.Item>
                ))}
              </Command.Group>
              {sessions.length > 0 && (
                <Command.Group heading="Sessions" className={groupCls}>
                  {sessions.slice(0, 20).map((s) => (
                    <Command.Item
                      key={s.id}
                      value={`session ${s.title} ${s.id}`}
                      className={itemCls}
                      onSelect={() => run(() => void navigate(`/session/${s.id}`))}
                    >
                      <span className="truncate">{s.title || "Untitled"}</span>
                      <span className="ml-auto font-mono text-xs text-muted-foreground">{s.agent}</span>
                    </Command.Item>
                  ))}
                </Command.Group>
              )}
              {agents.length > 0 && (
                <Command.Group heading="New session with agent" className={groupCls}>
                  {agents.map((a) => (
                    <Command.Item
                      key={a.name}
                      value={`agent ${a.name} ${a.description}`}
                      className={itemCls}
                      onSelect={() =>
                        run(() => {
                          void api.createSession({ agent: a.name }).then((s) => navigate(`/session/${s.id}`));
                        })
                      }
                    >
                      <span className="font-mono">{a.name}</span>
                      <span className="ml-auto truncate text-xs text-muted-foreground">{a.description}</span>
                    </Command.Item>
                  ))}
                </Command.Group>
              )}
              <RegistryGroups reg={reg} onSession={onSession} run={runKey} go={(to) => void navigate(to)} insert={insert} />
            </Command.List>
          </Command>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
