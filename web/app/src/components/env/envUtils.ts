import type { EnvApplied, EnvEntry } from "@sdk/env";
import type { BadgeVariant } from "@/components/ui/badge";

export const SOURCE_LABEL: Record<EnvEntry["source"], string> = {
  env: "environment",
  db: "database",
  secrets: "secrets.db",
  override: "startup flag",
  default: "default",
};

export const SOURCE_VARIANT: Record<EnvEntry["source"], BadgeVariant> = {
  env: "warning",
  db: "info",
  secrets: "success",
  override: "warning",
  default: "outline",
};

/** Text shown in an input for a current value (JSON for objects/arrays). */
export function draftOf(e: EnvEntry): string {
  if (e.value === null || e.value === undefined) return "";
  if (e.type === "json") return JSON.stringify(e.value, null, 2);
  return String(e.value);
}

export type Parsed = { ok: true; value: unknown } | { ok: false; error: string };

const PARSERS: Record<EnvEntry["type"], (raw: string) => Parsed> = {
  bool: (raw) => ({ ok: true, value: raw === "true" }),
  int: (raw) => (/^-?\d+$/.test(raw.trim()) ? { ok: true, value: Number(raw) } : { ok: false, error: "whole number" }),
  float: (raw) =>
    Number.isFinite(Number(raw)) && raw.trim() !== ""
      ? { ok: true, value: Number(raw) }
      : { ok: false, error: "number" },
  str: (raw) => ({ ok: true, value: raw }),
  enum: (raw) => ({ ok: true, value: raw }),
  json: (raw) => {
    try {
      return { ok: true, value: JSON.parse(raw) as unknown };
    } catch (err) {
      return { ok: false, error: `invalid JSON: ${(err as Error).message}` };
    }
  },
};

/** Draft text → wire value; an empty draft of a nullable entry resets it (null). */
export function parseDraft(e: EnvEntry, raw: string): Parsed {
  if (raw.trim() === "" && (e.nullable || e.type === "str")) return { ok: true, value: e.nullable ? null : "" };
  return PARSERS[e.type](raw);
}

export function appliedText(r: EnvApplied): string {
  return r.note;
}

export function matches(e: EnvEntry, q: string, onlySet: boolean): boolean {
  if (onlySet && !e.is_set) return false;
  if (!q) return true;
  const needle = q.toLowerCase();
  return e.name.toLowerCase().includes(needle) || e.description.toLowerCase().includes(needle);
}
