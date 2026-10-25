import type { HarnessEntry, SessionInfo } from "@sdk/client";
import type { CommandInfo } from "@sdk/contracts";
import { Command } from "cmdk";
import { Dialog as DialogPrimitive } from "radix-ui";
import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router";
import { api } from "@/lib/api";
import { NAV } from "@/components/nav";

export const INSERT_COMMAND_EVENT = "auoreo:insert-command";

const itemCls =
  "flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-sm aria-selected:bg-accent aria-selected:text-accent-foreground";
const groupCls =
  "[&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1 [&_[cmdk-group-heading]]:text-xs [&_[cmdk-group-heading]]:text-muted-foreground";

/** Cmd/Ctrl-K: pages, sessions, agents (new session), and slash commands (`GET /api/commands`). */
export function CommandPalette({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const navigate = useNavigate();
  const location = useLocation();
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [agents, setAgents] = useState<HarnessEntry[]>([]);
  const [commands, setCommands] = useState<CommandInfo[]>([]);
  const onSession = location.pathname.startsWith("/session/");

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    api.listSessions(50).then((s) => !cancelled && setSessions(s)).catch(() => undefined);
    api.harnessKind("agents").then((a) => !cancelled && setAgents(a)).catch(() => undefined);
    void api.supports("commands").then((ok) => {
      if (ok) api.commands().then((c) => !cancelled && setCommands(c)).catch(() => undefined);
    });
    return () => {
      cancelled = true;
    };
  }, [open]);

  const run = (fn: () => void) => {
    onOpenChange(false);
    fn();
  };

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
              placeholder="Jump to a page, session, agent or command…"
              className="h-10 border-b bg-transparent px-3 text-sm outline-none placeholder:text-muted-foreground"
            />
            <Command.List className="max-h-80 overflow-y-auto p-1">
              <Command.Empty className="py-6 text-center text-sm text-muted-foreground">No results.</Command.Empty>
              {onSession && commands.length > 0 && (
                <Command.Group heading="Commands" className={groupCls}>
                  {commands.map((c) => (
                    <Command.Item
                      key={c.name}
                      value={`/${c.name} ${c.description}`}
                      className={itemCls}
                      onSelect={() =>
                        run(() =>
                          window.dispatchEvent(new CustomEvent(INSERT_COMMAND_EVENT, { detail: `/${c.name} ` })),
                        )
                      }
                    >
                      <span className="font-mono">/{c.name}</span>
                      {c.args_hint && <span className="font-mono text-xs text-muted-foreground">{c.args_hint}</span>}
                      <span className="ml-auto truncate text-xs text-muted-foreground">{c.description}</span>
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
            </Command.List>
          </Command>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
