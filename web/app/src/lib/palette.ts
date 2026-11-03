import type { HarnessEntry } from "@sdk/client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

const FREQ_KEY = "auoreo_palette_freq";

/** Freq-first ordering (Codex rule): selection counts per palette key, kept per browser. */
export function readFreq(): Record<string, number> {
  try {
    return JSON.parse(localStorage.getItem(FREQ_KEY) ?? "{}") as Record<string, number>;
  } catch {
    return {};
  }
}

export function bumpFreq(key: string): void {
  try {
    const f = readFreq();
    f[key] = (f[key] ?? 0) + 1;
    localStorage.setItem(FREQ_KEY, JSON.stringify(f));
  } catch {
    /* storage blocked */
  }
}

export function byFreq<T>(items: readonly T[], key: (t: T) => string, freq: Record<string, number>): T[] {
  return [...items].sort((a, b) => (freq[key(b)] ?? 0) - (freq[key(a)] ?? 0));
}

/** Client-side session verbs that are not server slash commands (palette ↔ CLI parity). */
export const SESSION_VERBS = [
  { name: "steer", hint: "<text>", description: "steer the running turn with text" },
  { name: "queue", hint: "<text>", description: "queue a follow-up for after the running turn" },
  { name: "fork", hint: "", description: "fork this session into a new one" },
  { name: "resume", hint: "", description: "resume another session" },
] as const;

export interface Registries {
  skills: HarnessEntry[];
  plugins: { name: string; description: string }[];
  mcp: HarnessEntry[];
  models: string[];
}

/** Skills, plugins, MCP servers and models, loaded when the palette opens. */
export function useRegistries(open: boolean): Registries {
  const [r, setR] = useState<Registries>({ skills: [], plugins: [], mcp: [], models: [] });
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    const set = (patch: Partial<Registries>) => !cancelled && setR((x) => ({ ...x, ...patch }));
    api.skills().then((skills) => set({ skills })).catch(() => undefined);
    api.plugins().then((p) => set({ plugins: p.map(({ name, description }) => ({ name, description })) })).catch(() => undefined);
    api.harnessKind("mcp").then((mcp) => set({ mcp })).catch(() => undefined);
    api.models().then((m) => set({ models: m.data.map((x) => x.id) })).catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [open]);
  return r;
}


/**
 * cmdk filter for values shaped `kind|name` with the description in `keywords`: a name prefix
 * ranks first, then a name substring, then every search word found in the description. No
 * scattered-letter fuzzy matching, so "compact" no longer surfaces unrelated agents and plugins.
 */
export function paletteFilter(value: string, search: string, keywords?: string[]): number {
  const q = search.trim().toLowerCase().replace(/^\//, "");
  if (!q) return 1;
  const bar = value.indexOf("|");
  const kind = value.slice(0, Math.max(bar, 0)).toLowerCase();
  const name = value.slice(bar + 1).toLowerCase().replace(/^\//, "");
  if (name.startsWith(q)) return 1;
  if (name.includes(q)) return 0.7;
  const hay = [kind, name, ...(keywords ?? [])].join(" ").toLowerCase();
  return q.split(/\s+/).every((w) => hay.includes(w)) ? 0.2 : 0;
}

/**
 * While a search is typed, keep only the best `n` matches of a group (scored like
 * `paletteFilter`, stable for ties), so a word found in many descriptions cannot flood the list
 * with agents, skills or plugins. With no search the whole group shows.
 */
export function capGroup<T>(
  items: readonly T[],
  search: string,
  value: (t: T) => string,
  keywords?: (t: T) => string[] | undefined,
  n = 3,
): T[] {
  if (!search.trim()) return [...items];
  return items
    .map((t, i) => ({ t, i, score: paletteFilter(value(t), search, keywords?.(t)) }))
    .filter((x) => x.score > 0)
    .sort((a, b) => b.score - a.score || a.i - b.i)
    .slice(0, n)
    .map((x) => x.t);
}
