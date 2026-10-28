import type { Item, ToolItem } from "@/lib/thread";

/** A finished run of tool calls shown as one expandable row. */
export interface ToolGroupRow {
  kind: "toolgroup";
  key: string;
  tools: ToolItem[];
}
export type Row = Item | ToolGroupRow;

/** Runs shorter than this stay as individual cards. */
export const MIN_GROUP = 3;

const done = (it: Item): it is ToolItem => it.kind === "tool" && (it.status === "completed" || it.status === "error");

/**
 * Collapse runs of `MIN_GROUP`+ consecutive finished tool calls into one row. A run that is still
 * at the end of the thread is left alone (the agent may be mid-turn), and so is anything that
 * is pending, running or waiting for approval — those stay visible as their own cards.
 * The group is keyed by its first call, so it keeps its open/closed state as the run grows.
 */
export function groupToolRuns(items: readonly Item[]): Row[] {
  const out: Row[] = [];
  let i = 0;
  while (i < items.length) {
    const it = items[i] as Item;
    if (!done(it)) {
      out.push(it);
      i++;
      continue;
    }
    let j = i;
    while (j < items.length && done(items[j] as Item)) j++;
    const run = items.slice(i, j) as ToolItem[];
    if (run.length >= MIN_GROUP && j < items.length) out.push({ kind: "toolgroup", key: `g:${run[0]?.key}`, tools: run });
    else out.push(...run);
    i = j;
  }
  return out;
}

/** "read ×3 · grep ×2 · bash" — the most used tools first, at most `max` names. */
export function summarizeTools(tools: readonly ToolItem[], max = 3): string {
  const counts = new Map<string, number>();
  for (const t of tools) counts.set(t.name, (counts.get(t.name) ?? 0) + 1);
  const ranked = [...counts.entries()].sort((a, b) => b[1] - a[1]);
  const shown = ranked.slice(0, max).map(([n, c]) => (c > 1 ? `${n} ×${c}` : n));
  const more = ranked.length - max;
  return more > 0 ? `${shown.join(" · ")} · +${more} more` : shown.join(" · ");
}
