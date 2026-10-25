import { ApiError } from "@sdk/client";
import { api } from "@/lib/api";
import { errorText } from "@/lib/utils";

export interface OpNotice {
  level: "info" | "warn" | "error";
  text: string;
}

/** Window event: the command palette asks the open session page to run `/compact` now. */
export const RUN_COMPACT_EVENT = "auoreo:run-compact";

/**
 * `POST /api/sessions/{id}/compact`. 422 (nothing to compact / no shrink) and 409 (running)
 * are user-visible notices, not errors.
 */
export async function compactSession(id: string): Promise<OpNotice> {
  try {
    const r = await api.compact(id);
    return {
      level: "info",
      text: `Compacted ${r.pre_tokens.toLocaleString()} → ${r.post_tokens.toLocaleString()} tokens, archived ${r.archived}`,
    };
  } catch (e) {
    if (e instanceof ApiError && e.status === 422) return { level: "warn", text: `Compaction skipped: ${errorText(e)}` };
    if (e instanceof ApiError && e.status === 409) return { level: "warn", text: `Compaction skipped: ${errorText(e)}` };
    throw e;
  }
}
