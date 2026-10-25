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
}

export interface QuestionItem {
  readonly question: string;
  readonly header?: string;
  readonly options?: readonly { readonly label: string; readonly description?: string }[];
  readonly multiSelect?: boolean;
}

/** `question.asked` */
export interface QuestionAskedData {
  readonly request_id: string;
  readonly questions: readonly QuestionItem[];
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

/** `session.title_updated` */
export interface TitleUpdatedData {
  readonly title: string;
}
