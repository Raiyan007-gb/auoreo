/**
 * I5: unified diffs for file-editing tool cards (edit / str_replace / write / apply_patch),
 * computed from the call's arguments only — the browser never reads files.
 *
 * - edit, str_replace: `old_string` → `new_string` (LCS line diff, 3 lines of context);
 * - write: every line of `content` is an addition (the previous file is not known here);
 * - apply_patch: the patch text itself, lines classified by their `+`/`-`/`@@` prefix.
 */

export type DiffKind = "add" | "del" | "ctx" | "hunk" | "meta";
export interface DiffLine {
  kind: DiffKind;
  text: string;
}
export interface FileDiff {
  path: string;
  lines: DiffLine[];
  added: number;
  removed: number;
}

export const DIFF_TOOLS = new Set(["edit", "str_replace", "write", "apply_patch"]);
const MAX_LCS_CELLS = 250_000;
const CONTEXT = 3;

const lines = (s: string): string[] => (s === "" ? [] : s.replace(/\n$/, "").split("\n"));

/** Line-level LCS diff; very large inputs fall back to "all removed, then all added". */
export function lineDiff(a: string[], b: string[]): DiffLine[] {
  if (a.length * b.length > MAX_LCS_CELLS) {
    return [...a.map((text) => ({ kind: "del" as const, text })), ...b.map((text) => ({ kind: "add" as const, text }))];
  }
  const n = a.length;
  const m = b.length;
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) {
    const row = dp[i] as number[];
    const below = dp[i + 1] as number[];
    for (let j = m - 1; j >= 0; j--) row[j] = a[i] === b[j] ? (below[j + 1] ?? 0) + 1 : Math.max(below[j] ?? 0, row[j + 1] ?? 0);
  }
  const out: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      out.push({ kind: "ctx", text: a[i] ?? "" });
      i++;
      j++;
    } else if ((dp[i + 1]?.[j] ?? 0) >= (dp[i]?.[j + 1] ?? 0)) out.push({ kind: "del", text: a[i++] ?? "" });
    else out.push({ kind: "add", text: b[j++] ?? "" });
  }
  while (i < n) out.push({ kind: "del", text: a[i++] ?? "" });
  while (j < m) out.push({ kind: "add", text: b[j++] ?? "" });
  return out;
}

/** Keep `CONTEXT` lines around changes; longer unchanged runs collapse into one `hunk` marker. */
export function trimContext(diff: DiffLine[]): DiffLine[] {
  const keep = diff.map((d) => d.kind !== "ctx");
  const near = diff.map((_, i) => diff.slice(Math.max(0, i - CONTEXT), i + CONTEXT + 1).some((d) => d.kind !== "ctx"));
  const out: DiffLine[] = [];
  let skipped = 0;
  diff.forEach((d, i) => {
    if (keep[i] || near[i]) {
      if (skipped) out.push({ kind: "hunk", text: `… ${skipped} unchanged line${skipped === 1 ? "" : "s"}` });
      skipped = 0;
      out.push(d);
    } else skipped++;
  });
  if (skipped) out.push({ kind: "hunk", text: `… ${skipped} unchanged line${skipped === 1 ? "" : "s"}` });
  return out;
}

function summarize(path: string, ls: DiffLine[]): FileDiff {
  return {
    path,
    lines: ls,
    added: ls.filter((l) => l.kind === "add").length,
    removed: ls.filter((l) => l.kind === "del").length,
  };
}

const PATCH_KIND: [RegExp, DiffKind][] = [
  [/^(\*\*\* |diff |index |--- |\+\+\+ )/, "meta"],
  [/^@@/, "hunk"],
  [/^\+/, "add"],
  [/^-/, "del"],
];

function patchDiff(patch: string): FileDiff {
  const file = /^\*\*\* (?:Update|Add|Delete) File: (.+)$/m.exec(patch)?.[1] ?? /^\+\+\+ (?:b\/)?(.+)$/m.exec(patch)?.[1] ?? "patch";
  const ls = lines(patch).map((raw): DiffLine => {
    const kind = PATCH_KIND.find(([rx]) => rx.test(raw))?.[1] ?? "ctx";
    const strip = kind === "add" || kind === "del" || (kind === "ctx" && raw.startsWith(" "));
    return { kind, text: strip ? raw.slice(1) : raw };
  });
  return summarize(file, ls);
}

/** The diff a tool call would apply, or null for tools that do not edit files. */
export function toolDiff(tool: string, rawArgs: string): FileDiff | null {
  if (!DIFF_TOOLS.has(tool)) return null;
  let args: Record<string, unknown>;
  try {
    args = JSON.parse(rawArgs || "{}") as Record<string, unknown>;
  } catch {
    return null;
  }
  const s = (k: string) => (typeof args[k] === "string" ? (args[k] as string) : null);
  const path = s("path") ?? "";
  if (tool === "apply_patch") return s("patch") === null ? null : patchDiff(s("patch") ?? "");
  if (tool === "write") {
    const content = s("content");
    return content === null ? null : summarize(path, lines(content).map((text) => ({ kind: "add", text })));
  }
  const before = s("old_string");
  const after = s("new_string");
  if (before === null || after === null) return null;
  return summarize(path, trimContext(lineDiff(lines(before), lines(after))));
}

/** `-`/`+`/` ` prefixed text (the copyable unified form). */
export function unified(d: FileDiff): string {
  const sign: Record<DiffKind, string> = { add: "+", del: "-", ctx: " ", hunk: "", meta: "" };
  return d.lines.map((l) => `${sign[l.kind]}${l.text}`).join("\n");
}
