import { Command } from "cmdk";
import type { Registries } from "@/lib/palette";

export const PALETTE_ITEM =
  "flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-sm aria-selected:bg-accent aria-selected:text-accent-foreground";
export const PALETTE_GROUP =
  "[&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1 [&_[cmdk-group-heading]]:text-xs [&_[cmdk-group-heading]]:text-muted-foreground";

/** Skills / plugins / MCP / models groups. Models insert `/model <id>` on a session page. */
export function RegistryGroups({
  reg,
  onSession,
  run,
  go,
  insert,
}: {
  reg: Registries;
  onSession: boolean;
  run: (key: string, fn: () => void) => void;
  go: (to: string) => void;
  insert: (text: string) => void;
}) {
  return (
    <>
      {onSession && reg.models.length > 0 && (
        <Command.Group heading="Models" className={PALETTE_GROUP}>
          {reg.models.map((m) => (
            <Command.Item key={m} value={`model|${m}`} data-testid="palette-model" className={PALETTE_ITEM} onSelect={() => run(`model:${m}`, () => insert(`/model ${m}`))}>
              <span className="font-mono">/model</span>
              <span className="truncate font-mono text-xs">{m}</span>
            </Command.Item>
          ))}
        </Command.Group>
      )}
      {reg.skills.length > 0 && (
        <Command.Group heading="Skills" className={PALETTE_GROUP}>
          {reg.skills.map((s) => (
            <Command.Item key={s.name} value={`skill|${s.name}`} keywords={[s.description]} data-testid="palette-skill" className={PALETTE_ITEM} onSelect={() => run(`skill:${s.name}`, () => go("/skills"))}>
              <span className="font-mono">{s.name}</span>
              <span className="ml-auto truncate text-xs text-muted-foreground">{s.description}</span>
            </Command.Item>
          ))}
        </Command.Group>
      )}
      {(reg.plugins.length > 0 || reg.mcp.length > 0) && (
        <Command.Group heading="Plugins & MCP" className={PALETTE_GROUP}>
          {reg.plugins.map((p) => (
            <Command.Item key={`p:${p.name}`} value={`plugin|${p.name}`} keywords={[p.description]} data-testid="palette-plugin" className={PALETTE_ITEM} onSelect={() => run(`plugin:${p.name}`, () => go("/plugins"))}>
              <span className="font-mono">{p.name}</span>
              <span className="text-xs text-muted-foreground">plugin</span>
              <span className="ml-auto truncate text-xs text-muted-foreground">{p.description}</span>
            </Command.Item>
          ))}
          {reg.mcp.map((m) => (
            <Command.Item key={`m:${m.name}`} value={`mcp|${m.name}`} keywords={[m.description]} data-testid="palette-mcp" className={PALETTE_ITEM} onSelect={() => run(`mcp:${m.name}`, () => go("/plugins"))}>
              <span className="font-mono">{m.name}</span>
              <span className="text-xs text-muted-foreground">MCP</span>
              <span className="ml-auto truncate text-xs text-muted-foreground">{m.description}</span>
            </Command.Item>
          ))}
        </Command.Group>
      )}
    </>
  );
}
