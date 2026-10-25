/**
 * TEMPORARY hand-written contracts for endpoints planned in docs/roadmap-m3-plus.md
 * (M3a compaction, M3c sessions/usage, M3d commands, M3b WebSocket) that are NOT yet in
 * `openapi.json`. Every type here must be deleted once `npm run gen:types` emits it in
 * `types.gen.ts`. The UI probes each endpoint once and degrades gracefully when absent.
 */
import type { components } from "./types.gen";

type SessionInfo = components["schemas"]["SessionInfo"];

/** One planned endpoint: method + path template, used by the capability probe. */
export interface PlannedEndpoint {
  readonly method: "GET" | "POST" | "DELETE";
  readonly path: string;
}

export const PLANNED = {
  fork: { method: "POST", path: "/api/sessions/{session_id}/fork" },
  archive: { method: "POST", path: "/api/sessions/{session_id}/archive" },
  unarchive: { method: "POST", path: "/api/sessions/{session_id}/unarchive" },
  deleteSession: { method: "DELETE", path: "/api/sessions/{session_id}" },
  export: { method: "GET", path: "/api/sessions/{session_id}/export" },
  compact: { method: "POST", path: "/api/sessions/{session_id}/compact" },
  usage: { method: "GET", path: "/api/usage" },
  usageSummary: { method: "GET", path: "/api/usage/summary" },
  commands: { method: "GET", path: "/api/commands" },
} as const satisfies Record<string, PlannedEndpoint>;

export type PlannedName = keyof typeof PLANNED;

/** POST /api/sessions/{id}/fork → SessionInfo (M3c). */
export type ForkResponse = SessionInfo;
/** POST /api/sessions/{id}/archive|unarchive → SessionInfo (M3c). */
export type ArchiveResponse = SessionInfo;
/** GET /api/sessions/{id}/export?format=json|md (M3c). */
export type ExportFormat = "json" | "md";

/** POST /api/sessions/{id}/compact (M3a). */
export interface CompactResult {
  readonly pre_tokens: number;
  readonly post_tokens: number;
  readonly archived: number;
  readonly compaction_id: string;
}

/** GET /api/usage?session_id= → rows (M3c usage ledger). */
export interface UsageRow {
  readonly session_id: string;
  readonly run_id: string;
  readonly model: string;
  readonly prompt_tokens: number;
  readonly completion_tokens: number;
  readonly cost_usd: number | null;
  readonly estimated: boolean;
  readonly created_at: string;
}

export interface UsageByModel {
  readonly model: string;
  readonly tokens: number;
  readonly cost_usd: number;
  readonly runs: number;
}

export interface UsageBySession {
  readonly session_id: string;
  readonly title?: string | null;
  readonly tokens: number;
  readonly cost_usd: number;
  readonly runs: number;
}

/** GET /api/usage/summary?days= (M3c). */
export interface UsageSummary {
  readonly total_tokens: number;
  readonly total_cost_usd: number;
  readonly runs: number;
  readonly by_model: readonly UsageByModel[];
  readonly by_session: readonly UsageBySession[];
}

/** GET /api/commands (M3d). The composer sends `/name args` as plain message text. */
export interface CommandInfo {
  readonly name: string;
  readonly description: string;
  readonly handler: string;
  readonly args_hint: string | null;
}

/** Event `data` payloads the UI reads (src/auoreo/runtime/runner.py, core/approvals.py). */
export interface PermissionRequestedData {
  readonly request_id: string;
  readonly tool: string;
  readonly permission: string;
  readonly pattern: string;
  readonly detail: string;
}

export interface QuestionItem {
  readonly question: string;
  readonly header?: string;
  readonly options?: readonly { readonly label: string; readonly description?: string }[];
  readonly multiSelect?: boolean;
}

export interface QuestionAskedData {
  readonly request_id: string;
  readonly questions: readonly QuestionItem[];
}
