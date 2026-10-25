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

