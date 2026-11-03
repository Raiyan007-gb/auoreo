import type { HarnessEntry } from "@sdk/client";
import { Command } from "cmdk";
import { capGroup, type Registries } from "@/lib/palette";

export const PALETTE_ITEM =
  "flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-sm aria-selected:bg-accent aria-selected:text-accent-foreground";
export const PALETTE_GROUP =
  "[&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1 [&_[cmdk-group-heading]]:text-xs [&_[cmdk-group-heading]]:text-muted-foreground";

type Ext = { kind: "plugin"; name: string; description: string } | { kind: "mcp"; entry: HarnessEntry };

/**
 * Skills / plugins / MCP / models groups. Models insert `/model <id>` on a session page.
 * While `search` is typed each group shows at most its three best matches (`capGroup`).
 */
export function RegistryGroups({
  reg,
  search,
  onSession,
  run,
  go,
  insert,
}: {
  reg: Registries;
  search: string;
  onSession: boolean;
  run: (key: string, fn: () => void) => void;
  go: (to: string) => void;
  insert: (text: string) => void;
}) {
  const models = capGroup(reg.models, search, (m) => `model|${m}`);
  const skills = capGroup(
    reg.skills,
    search,
    (s) => `skill|${s.name}`,
    (s) => [s.description],
  );
  const ext: Ext[] = [
    ...reg.plugins.map((p): Ext => ({ kind: "plugin", name: p.name, description: p.description })),
    ...reg.mcp.map((m): Ext => ({ kind: "mcp", entry: m })),
  ];
  const extensions = capGroup(
    ext,
    search,
    (x) => (x.kind === "plugin" ? `plugin|${x.name}` : `mcp|${x.entry.name}`),
    (x) => [x.kind === "plugin" ? x.description : x.entry.description],
  );
  return (
    <>
      {onSession && models.length > 0 && (
        <Command.Group heading="Models" className={PALETTE_GROUP}>
          {models.map((m) => (
            <Command.Item key={m} value={`model|${m}`} data-testid="palette-model" className={PALETTE_ITEM} onSelect={() => run(`model:${m}`, () => insert(`/model ${m}`))}>
              <span className="font-mono">/model</span>
              <span className="truncate font-mono text-xs">{m}</span>
            </Command.Item>
          ))}
        </Command.Group>
      )}
      {skills.length > 0 && (
        <Command.Group heading="Skills" className={PALETTE_GROUP}>
          {skills.map((s) => (
            <Command.Item key={s.name} value={`skill|${s.name}`} keywords={[s.description]} data-testid="palette-skill" className={PALETTE_ITEM} onSelect={() => run(`skill:${s.name}`, () => go("/skills"))}>
              <span className="font-mono">{s.name}</span>
              <span className="ml-auto truncate text-xs text-muted-foreground">{s.description}</span>
            </Command.Item>
          ))}
        </Command.Group>
      )}
      {extensions.length > 0 && (
        <Command.Group heading="Plugins & MCP" className={PALETTE_GROUP}>
          {extensions.map((x) =>
            x.kind === "plugin" ? (
              <Command.Item key={`p:${x.name}`} value={`plugin|${x.name}`} keywords={[x.description]} data-testid="palette-plugin" className={PALETTE_ITEM} onSelect={() => run(`plugin:${x.name}`, () => go("/plugins"))}>
                <span className="font-mono">{x.name}</span>
                <span className="text-xs text-muted-foreground">plugin</span>
                <span className="ml-auto truncate text-xs text-muted-foreground">{x.description}</span>
              </Command.Item>
            ) : (
              <Command.Item key={`m:${x.entry.name}`} value={`mcp|${x.entry.name}`} keywords={[x.entry.description]} data-testid="palette-mcp" className={PALETTE_ITEM} onSelect={() => run(`mcp:${x.entry.name}`, () => go("/plugins"))}>
                <span className="font-mono">{x.entry.name}</span>
                <span className="text-xs text-muted-foreground">MCP</span>
                <span className="ml-auto truncate text-xs text-muted-foreground">{x.entry.description}</span>
              </Command.Item>
            ),
          )}
        </Command.Group>
      )}
    </>
  );
}
