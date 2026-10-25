/**
 * Thread model for `/session/:id`: a pure reducer over ServerEvents (runner.py emits
 * run.*, message.*, tool.*, usage, permission.*, question.*), plus a builder from the
 * `GET /api/sessions/{id}/messages` snapshot for first paint.
 */
import type { MessageOut, ServerEvent } from "@sdk/client";
import type {
  CompactionCompletedData,
  CompactionFailedData,
  CompactionSkippedData,
  PermissionRequestedData,
  QuestionAskedData,
  RunErrorData,
} from "@sdk/events";

export type ToolStatus = "pending" | "running" | "completed" | "error";

export type Item =
  | { kind: "user"; key: string; text: string; steer?: boolean }
  | { kind: "assistant"; key: string; text: string; streaming: boolean; runId?: string }
  | {
      kind: "tool";
      key: string;
      runId: string;
      callId: string;
      name: string;
      args: string;
      status: ToolStatus;
      result?: string;
      meta?: Record<string, unknown>;
    }
  | { kind: "notice"; key: string; level: "info" | "warn" | "error"; text: string; code?: string };

export type ToolItem = Extract<Item, { kind: "tool" }>;

export interface Usage {
  prompt: number;
  completion: number;
  cost: number;
}

export interface ThreadState {
  items: Item[];
  lastSeq: number;
  usage: Usage;
  running: boolean;
  runId?: string;
  agent?: string;
  model?: string;
  /** Live title from `session.title_updated` (overrides the REST snapshot). */
  title?: string;
  archived?: boolean;
  /** Set by `session.forked`: messages 0..forkedAt were copied (they have no events here). */
  forkedAt?: number;
  permissions: Record<string, PermissionRequestedData>;
  questions: Record<string, QuestionAskedData>;
}

export const emptyThread = (): ThreadState => ({
  items: [],
  lastSeq: 0,
  usage: { prompt: 0, completion: 0, cost: 0 },
  running: false,
  permissions: {},
  questions: {},
});

const str = (v: unknown): string => (typeof v === "string" ? v : v == null ? "" : JSON.stringify(v));
const num = (v: unknown): number => (typeof v === "number" && Number.isFinite(v) ? v : 0);

function lastStreaming(items: Item[], runId: string): number {
  for (let i = items.length - 1; i >= 0; i--) {
    const it = items[i];
    if (it?.kind === "assistant" && it.streaming && it.runId === runId) return i;
    if (it?.kind === "user") break;
  }
  return -1;
}

function findTool(items: Item[], runId: string, callId: string): number {
  for (let i = items.length - 1; i >= 0; i--) {
    const it = items[i];
    if (it?.kind === "tool" && it.callId === callId && it.runId === runId) return i;
  }
  return -1;
}

function replaceAt(items: Item[], i: number, next: Item): Item[] {
  const copy = items.slice();
  copy[i] = next;
  return copy;
}

const FAIL_LABEL: Record<string, string> = {
  "run.failed": "Run failed",
  "run.blocked": "Run blocked",
  "run.aborted": "Run interrupted",
  "run.max_steps": "Stopped at the step limit",
};

/** Human explanations for `run.blocked` codes (runtime/runner.py). */
const BLOCK_TEXT: Record<string, string> = {
  BUDGET: "the session's spend budget is exhausted — raise the cap in settings or start a new session",
  DOOM_LOOP: "the agent kept repeating the same tool call, so the run was stopped",
  PERMISSION_REJECTED: "a tool call was rejected, so the run stopped",
};

const notice = (s: ThreadState, e: ServerEvent, level: "info" | "warn" | "error", text: string, code?: string): ThreadState => ({
  ...s,
  items: [...s.items, { kind: "notice", key: `n${e.seq}`, level, text, code }],
});

type Handler = (s: ThreadState, d: Record<string, unknown>, e: ServerEvent) => ThreadState;

function finishRun(s: ThreadState, d: Record<string, unknown>, e: ServerEvent): ThreadState {
  const runId = str(d.run_id);
  let items = s.items.map((it) =>
    it.kind === "assistant" && it.streaming && it.runId === runId ? { ...it, streaming: false } : it,
  );
  items = items.map((it) =>
    it.kind === "tool" && it.runId === runId && (it.status === "pending" || it.status === "running")
      ? { ...it, status: "error" as const, result: it.result ?? "not completed" }
      : it,
  );
  const label = FAIL_LABEL[e.type];
  if (label) {
    const err = (d.error as RunErrorData | undefined) ?? (d.code ? { code: str(d.code), message: str(d.message) } : undefined);
    const explained = err?.code ? BLOCK_TEXT[err.code] : undefined;
    const text = explained
      ? `${label}: ${explained}${err?.message ? ` (${err.message})` : ""}`
      : err
        ? `${label}: ${err.code ?? ""} ${err.message ?? ""}`.trim()
        : label;
    items = [
      ...items,
      { kind: "notice", key: `n${e.seq}`, level: e.type === "run.max_steps" ? "warn" : "error", text, code: err?.code },
    ];
  }
  // A terminal run event means no approval or question for this run can still be answered.
  return { ...s, items, running: false, permissions: {}, questions: {} };
}

const HANDLERS: Record<string, Handler> = {
  "run.started": (s, d) => ({
    ...s,
    running: true,
    runId: str(d.run_id),
    agent: str(d.agent) || s.agent,
    model: str(d.model) || s.model,
  }),
  "run.completed": finishRun,
  "run.failed": finishRun,
  "run.blocked": finishRun,
  "run.aborted": finishRun,
  "run.max_steps": finishRun,
  "message.created": (s, d, e) =>
    d.role === "user"
      ? {
          ...s,
          items: [...s.items, { kind: "user", key: `u${e.seq}`, text: str(d.content), steer: d.steer === true }],
        }
      : s,
  "message.delta": (s, d, e) => {
    const runId = str(d.run_id);
    const i = lastStreaming(s.items, runId);
    const cur = i >= 0 ? s.items[i] : undefined;
    if (cur?.kind === "assistant" && i === s.items.length - 1) {
      return { ...s, items: replaceAt(s.items, i, { ...cur, text: cur.text + str(d.text) }) };
    }
    return {
      ...s,
      items: [...s.items, { kind: "assistant", key: `a${e.seq}`, text: str(d.text), streaming: true, runId }],
    };
  },
  "message.completed": (s, d, e) => {
    const runId = str(d.run_id);
    const content = str(d.content);
    let items = s.items;
    const i = lastStreaming(items, runId);
    const cur = i >= 0 ? items[i] : undefined;
    if (cur?.kind === "assistant") {
      items = content || cur.text ? replaceAt(items, i, { ...cur, text: content || cur.text, streaming: false }) : items.filter((_, j) => j !== i);
    } else if (content) {
      items = [...items, { kind: "assistant", key: `a${e.seq}`, text: content, streaming: false, runId }];
    }
    const calls = Array.isArray(d.tool_calls) ? (d.tool_calls as Record<string, unknown>[]) : [];
    for (const c of calls) {
      const callId = str(c.id);
      if (findTool(items, runId, callId) >= 0) continue;
      items = [
        ...items,
        { kind: "tool", key: `t${runId}:${callId}`, runId, callId, name: str(c.name), args: str(c.arguments), status: "pending" },
      ];
    }
    return { ...s, items };
  },
  "tool.started": (s, d) => {
    const runId = str(d.run_id);
    const callId = str(d.call_id);
    const i = findTool(s.items, runId, callId);
    const cur = i >= 0 ? s.items[i] : undefined;
    if (cur?.kind === "tool") {
      return { ...s, items: replaceAt(s.items, i, { ...cur, status: "running", args: str(d.arguments) || cur.args }) };
    }
    return {
      ...s,
      items: [
        ...s.items,
        { kind: "tool", key: `t${runId}:${callId}`, runId, callId, name: str(d.tool), args: str(d.arguments), status: "running" },
      ],
    };
  },
  "tool.completed": (s, d) => {
    const runId = str(d.run_id);
    const callId = str(d.call_id);
    const i = findTool(s.items, runId, callId);
    const base: ToolItem =
      i >= 0 && s.items[i]?.kind === "tool"
        ? (s.items[i] as ToolItem)
        : { kind: "tool", key: `t${runId}:${callId}`, runId, callId, name: str(d.tool), args: "", status: "running" };
    const next: ToolItem = {
      ...base,
      status: d.is_error === true ? "error" : "completed",
      result: str(d.content),
      meta: (d.meta as Record<string, unknown> | undefined) ?? undefined,
    };
    return { ...s, items: i >= 0 ? replaceAt(s.items, i, next) : [...s.items, next] };
  },
  usage: (s, d) => ({
    ...s,
    model: str(d.model) || s.model,
    usage: {
      prompt: s.usage.prompt + num(d.prompt_tokens),
      completion: s.usage.completion + num(d.completion_tokens),
      cost: s.usage.cost + num(d.cost_usd),
    },
  }),
  "permission.requested": (s, d) => ({
    ...s,
    permissions: { ...s.permissions, [str(d.request_id)]: d as unknown as PermissionRequestedData },
  }),
  "permission.replied": (s, d) => {
    const { [str(d.request_id)]: _gone, ...rest } = s.permissions;
    return { ...s, permissions: rest };
  },
  "question.asked": (s, d) => ({
    ...s,
    questions: { ...s.questions, [str(d.request_id)]: d as unknown as QuestionAskedData },
  }),
  "question.answered": (s, d) => {
    const { [str(d.request_id)]: _gone, ...rest } = s.questions;
    return { ...s, questions: rest };
  },
  "compaction.started": (s, d, e) =>
    notice(s, e, "info", `Compacting context (${str(d.trigger) || "manual"})…`, "compaction.started"),
  "compaction.completed": (s, d, e) => {
    const c = d as unknown as CompactionCompletedData;
    return notice(
      s,
      e,
      "info",
      `Context compacted: ${num(c.pre_tokens).toLocaleString()} → ${num(c.post_tokens).toLocaleString()} tokens, archived ${num(c.archived)} messages`,
      "compaction.completed",
    );
  },
  "compaction.failed": (s, d, e) => {
    const c = d as unknown as CompactionFailedData;
    return notice(s, e, "warn", `Compaction did not run: ${str(c.reason)}`, "compaction.failed");
  },
  "compaction.skipped": (s, d, e) => {
    const c = d as unknown as CompactionSkippedData;
    return notice(s, e, "warn", `Compaction skipped: ${str(c.reason)}`, "compaction.skipped");
  },
  "session.title_updated": (s, d) => ({ ...s, title: str(d.title) || s.title }),
  "session.model_changed": (s, d, e) => ({
    ...notice(s, e, "info", `Model set to ${str(d.model) || "default"}`, "session.model_changed"),
    model: str(d.model) || undefined,
  }),
  "session.archived": (s) => ({ ...s, archived: true }),
  "session.unarchived": (s) => ({ ...s, archived: false }),
  "session.forked": (s, d, e) => ({
    ...notice(s, e, "info", `Forked from ${str(d.parent_id)}`, "session.forked"),
    forkedAt: typeof d.at_idx === "number" ? d.at_idx : -1,
  }),
};

export function reduceEvent(s: ThreadState, e: ServerEvent): ThreadState {
  if (e.seq <= s.lastSeq) return s;
  const h = HANDLERS[e.type];
  const next = h ? h(s, e.data, e) : s;
  return { ...next, lastSeq: e.seq };
}

export function reduceEvents(s: ThreadState, events: readonly ServerEvent[]): ThreadState {
  return events.reduce(reduceEvent, s);
}

/** First paint from the persisted history (no live state, no usage). */
export function fromMessages(messages: readonly MessageOut[]): Item[] {
  const items: Item[] = [];
  const byCall = new Map<string, number>();
  for (const m of messages) {
    if (m.role === "user") items.push({ kind: "user", key: `m${m.idx}`, text: m.content ?? "" });
    else if (m.role === "assistant") {
      if (m.content) items.push({ kind: "assistant", key: `m${m.idx}`, text: m.content, streaming: false });
      for (const c of m.tool_calls ?? []) {
        const fn = (c.function ?? {}) as { name?: string; arguments?: string };
        const callId = str(c.id);
        byCall.set(callId, items.length);
        items.push({
          kind: "tool",
          key: `m${m.idx}:${callId}`,
          runId: `m${m.idx}`,
          callId,
          name: fn.name ?? "tool",
          args: fn.arguments ?? "",
          status: "pending",
        });
      }
    } else if (m.role === "tool" && m.tool_call_id) {
      const i = byCall.get(m.tool_call_id);
      const it = i === undefined ? undefined : items[i];
      if (i !== undefined && it?.kind === "tool") items[i] = { ...it, status: "completed", result: m.content ?? "" };
    }
  }
  return items;
}
