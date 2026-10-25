import { truncate } from "@/lib/utils";

/** The most telling argument for a one-line preview (parameter names from each tool.yaml). */
const PRIMARY_KEYS = ["command", "path", "file_path", "pattern", "url", "query", "name", "questions", "patch"];

function parseArgs(args: string): Record<string, unknown> | null {
  try {
    const v: unknown = JSON.parse(args || "{}");
    return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

export function argsPreview(args: string): string {
  const obj = parseArgs(args);
  if (!obj) return truncate(args.replace(/\s+/g, " "), 100);
  for (const k of PRIMARY_KEYS) {
    const v = obj[k];
    if (typeof v === "string") return truncate(v.replace(/\s+/g, " "), 100);
    if (v !== undefined) return truncate(JSON.stringify(v), 100);
  }
  const first = Object.entries(obj)[0];
  return first ? truncate(`${first[0]}: ${JSON.stringify(first[1])}`, 100) : "";
}

export function prettyArgs(args: string): string {
  const obj = parseArgs(args);
  return obj ? JSON.stringify(obj, null, 2) : args;
}
