/**
 * Event `data` payload shapes the UI reads. These ride inside the `ServerEvent` envelope
 * (SSE `GET /api/events` and WS `/ws`) and are NOT part of `openapi.json`, so they are
 * hand-written here; every REST type comes from `types.gen.ts` instead.
 * Sources: src/auoreo/core/approvals.py, runtime/runner.py, runtime/compaction.py,
 * runtime/titles.py, core/sessions_ops.py.
 */

/** `permission.requested` */
export interface PermissionRequestedData {
  readonly request_id: string;
  readonly tool: string;
  readonly permission: string;
  readonly pattern: string;
  readonly detail: string;
  /** Single-use request (e.g. sandbox escalation): no "Always" option. */
  readonly one_shot?: boolean;
  readonly risk?: string;
  /** I5: the reply may carry edited arguments (`updated_input`). */
  readonly editable?: boolean;
  /** I5: the call's arguments (redacted) for the edit form. */
  readonly input?: Record<string, unknown> | null;
  /** I5: why the agent asks (sandbox escalation justification). */
  readonly justification?: string | null;
}

/** `mcp.elicitation.open` (src/auoreo/mcp/elicitation.py): the user consented in the card. */
export interface ElicitationOpenData {
  readonly server: string;
  readonly url: string;
  readonly domain: string;
  readonly elicitation_id: string;
}

export interface QuestionItem {
  readonly question: string;
  readonly header?: string;
  readonly options?: readonly { readonly label: string; readonly description?: string }[];
  readonly multiSelect?: boolean;
  /** Free-text answer allowed (default true); returned as `customText`. */
  readonly allowCustom?: boolean;
  /** `exit_plan_mode`: the plan to review, as Markdown. */
  readonly plan?: string;
}

/** `question.asked` */
export interface QuestionAskedData {
  readonly request_id: string;
  readonly questions: readonly QuestionItem[];
  /** Seconds the agent waits before `timed_out` (ask_user_question default 60). */
  readonly timeout_s?: number;
  /** Unix seconds when the question expires. */
  readonly expires_at?: number;
}

/** One `update_plan` step (`plan.updated` → `steps[]`). */
export interface PlanStepData {
  readonly step: string;
  readonly status: "pending" | "in_progress" | "completed";
  readonly done_when?: string;
}

/** `plan.updated` */
export interface PlanUpdatedData {
  readonly run_id?: string;
  readonly steps: readonly PlanStepData[];
  readonly explanation?: string;
}

/** `run.blocked` / `run.failed` → `data.error` (run.failed may carry top-level code/message). */
export interface RunErrorData {
  readonly code?: string;
  readonly message?: string;
}

/** `compaction.completed` */
export interface CompactionCompletedData {
  readonly trigger: string;
  readonly compaction_id: string;
  readonly pre_tokens: number;
  readonly post_tokens: number;
  readonly archived: number;
  readonly pruned: number;
  readonly summary_chars: number;
}

/** `compaction.failed` */
export interface CompactionFailedData {
  readonly trigger: string;
  readonly compaction_id: string;
  readonly reason: string;
  readonly failures: number;
}

/** `compaction.skipped` — benign: every message was protected (short session). */
export interface CompactionSkippedData {
  readonly trigger: string;
  readonly compaction_id: string;
  readonly reason: string;
}

/** `session.title_updated` */
export interface TitleUpdatedData {
  readonly title: string;
}
