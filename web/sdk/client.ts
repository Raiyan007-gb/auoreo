/**
 * Typed client for the auoreo API: REST (types from `types.gen.ts`), the durable SSE event
 * stream (`GET /api/events`) and the WebSocket control plane (`/ws`, src/auoreo/schema/ws.py).
 * Framework-agnostic: no React here.
 */
import { type ControlOptions, connectControl } from "./control";
// --- W0-A
import { opsMethods } from "./ops";
// --- end W0-A
import { statusMethods } from "./ops2"; // --- W0-B
import type { components, paths } from "./types.gen";

type S = components["schemas"];
export type SessionInfo = S["SessionInfo"];
export type SessionCreate = S["SessionCreate"];
export type MessageOut = S["MessageOut"];
export type ChatRequest = S["ChatRequest"];
export type ChatAccepted = S["ChatAccepted"];
export type HarnessEntry = S["HarnessEntry"];
export type HarnessListing = S["HarnessListing"];
export type ReloadResult = S["ReloadResult"];
export type Health = S["Health"];
export type SearchHit = S["SearchHit"];
export type FactIn = S["FactIn"];
export type PendingPermission = S["PendingPermission"];
export type PendingQuestion = S["PendingQuestionOut"];
export type PermissionReplyBody = S["PermissionReply"];
export type PermissionReplyKind = "once" | "always" | "reject";
export type SessionDetail = S["SessionDetail"];
export type SessionDeleted = S["SessionDeleted"];
export type CompactionOut = S["CompactionOut"];
export type CompactRequest = S["CompactRequest"];
export type ArchivedMessage = S["ArchivedMessage"];
export type UsageRecord = S["UsageRecord"];
export type UsageSummary = S["UsageSummary"];
export type UsageTotals = S["UsageTotals"];
export type CommandInfo = S["CommandInfo"];
export type LatestSeq = S["LatestSeq"];
export type AuthStatus = S["AuthStatus"]; // --- G1a
/** `?format=` of GET /api/sessions/{id}/export (from the OpenAPI query parameter). */
export type ExportFormat = NonNullable<
  NonNullable<paths["/api/sessions/{session_id}/export"]["get"]["parameters"]["query"]>["format"]
>;

/** SSE/WS envelope — mirrors `src/auoreo/schema/events.py::ServerEvent` (SSE has no OpenAPI schema). */
export interface ServerEvent {
  readonly id: string;
  readonly seq: number;
  readonly type: string;
  readonly aggregate_id: string;
  readonly aggregate_seq: number;
  readonly data: Record<string, unknown>;
  readonly created_at: string;
}

/** SSE event names equal `ServerEvent.type`; EventSource needs one listener per name. */
export const EVENT_TYPES = [
  "session.created",
  "session.updated",
  "session.forked",
  "session.fork_created",
  "session.archived",
  "session.unarchived",
  "session.deleted",
  "session.title_updated",
  "session.model_changed",
  "run.started",
  "run.completed",
  "run.failed",
  "run.blocked",
  "run.aborted",
  "run.max_steps",
  "step.started",
  "message.created",
  "message.delta",
  "message.completed",
  "tool.started",
  "tool.progress",
  "tool.completed",
  "usage",
  "permission.requested",
  "permission.replied",
  "question.asked",
  "question.answered",
  "workflow.started",
  "workflow.step",
  "workflow.completed",
  "compaction.started",
  "compaction.completed",
  "compaction.failed",
  "compaction.skipped",
  "harness.reloaded",
  "message",
] as const;

export const TERMINAL_RUN_EVENTS: ReadonlySet<string> = new Set([
  "run.completed",
  "run.failed",
  "run.blocked",
  "run.aborted",
  "run.max_steps",
]);

export class ApiError extends Error {
  readonly status: number;
  readonly detail: unknown;
  constructor(status: number, detail: unknown) {
    super(typeof detail === "string" ? detail : `HTTP ${status}`);
    this.status = status;
    this.detail = detail;
  }
}

export interface ClientOptions {
  /** Origin of the server; default is same-origin (relative URLs). */
  readonly baseUrl?: string;
  readonly getToken?: () => string | null;
}

type Query = Record<string, string | number | boolean | readonly string[] | undefined | null>;

interface RequestInit2 {
  readonly query?: Query;
  readonly body?: unknown;
  readonly headers?: Record<string, string>;
  readonly signal?: AbortSignal;
  readonly raw?: boolean;
}

function buildQuery(query: Query | undefined): string {
  if (!query) return "";
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v === undefined || v === null) continue;
    if (Array.isArray(v)) for (const item of v as readonly string[]) qs.append(k, item);
    else qs.append(k, String(v));
  }
  const s = qs.toString();
  return s ? `?${s}` : "";
}

const enc = encodeURIComponent;

export function createClient(opts: ClientOptions = {}) {
  const base = (opts.baseUrl ?? "").replace(/\/$/, "");
  const token = () => opts.getToken?.() ?? null;

  async function request<T>(method: string, path: string, init: RequestInit2 = {}): Promise<T> {
    const headers: Record<string, string> = { Accept: "application/json", ...init.headers };
    const t = token();
    if (t) headers.Authorization = `Bearer ${t}`;
    let body: string | undefined;
    if (init.body !== undefined) {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(init.body);
    }
    const res = await fetch(`${base}${path}${buildQuery(init.query)}`, {
      method,
      headers,
      body,
      signal: init.signal,
    });
    if (!res.ok) {
      let detail: unknown = res.statusText;
      try {
        const j = (await res.json()) as { detail?: unknown };
        detail = j.detail ?? j;
      } catch {
        /* non-JSON error body */
      }
      throw new ApiError(res.status, detail);
    }
    if (init.raw) return res as unknown as T;
    if (res.status === 204) return undefined as T;
    const text = await res.text();
    return (text ? JSON.parse(text) : undefined) as T;
  }

  return {
    request,
    token,
    baseUrl: base,

    health: () => request<Health>("GET", "/api/health"),
    // --- G1a: HttpOnly cookie for EventSource / WS / the token meta in index.html
    login: (password: string) => request<AuthStatus>("POST", "/api/auth/login", { body: { password } }),
    logout: () => request<AuthStatus>("POST", "/api/auth/logout"),
    authStatus: () => request<AuthStatus>("GET", "/api/auth/status"),

    // sessions
    /** `archived: true` returns archived AND active sessions (the server's `?archived=true`). */
    listSessions: (limit = 200, archived = false) =>
      request<SessionInfo[]>("GET", "/api/sessions", { query: { limit, archived: archived || undefined } }),
    createSession: (body: SessionCreate) => request<SessionInfo>("POST", "/api/sessions", { body }),
    getSession: (id: string) => request<SessionDetail>("GET", `/api/sessions/${enc(id)}`),
    messages: (id: string) => request<MessageOut[]>("GET", `/api/sessions/${enc(id)}/messages`),
    cancel: (id: string) =>
      request<Record<string, boolean>>("POST", `/api/sessions/${enc(id)}/cancel`),
    /** `mode` defaults to "followup" server-side (openapi-typescript marks defaults required). */
    chat: (body: Omit<ChatRequest, "mode"> & { mode?: ChatRequest["mode"] }, idempotencyKey?: string) =>
      request<ChatAccepted>("POST", "/api/chat", {
        body: { mode: "followup", ...body } satisfies ChatRequest,
        headers: idempotencyKey ? { "Idempotency-Key": idempotencyKey } : undefined,
      }),

    // session ops, compaction, usage, commands
    fork: (id: string) => request<SessionDetail>("POST", `/api/sessions/${enc(id)}/fork`),
    archive: (id: string) => request<SessionDetail>("POST", `/api/sessions/${enc(id)}/archive`),
    unarchive: (id: string) => request<SessionDetail>("POST", `/api/sessions/${enc(id)}/unarchive`),
    deleteSession: (id: string) => request<SessionDeleted>("DELETE", `/api/sessions/${enc(id)}`),
    exportSession: (id: string, format: ExportFormat) =>
      request<Response>("GET", `/api/sessions/${enc(id)}/export`, { query: { format }, raw: true }),
    compact: (id: string, body?: CompactRequest) =>
      request<CompactionOut>("POST", `/api/sessions/${enc(id)}/compact`, { body }),
    archivedMessages: (id: string, limit = 1000) =>
      request<ArchivedMessage[]>("GET", `/api/sessions/${enc(id)}/archive`, { query: { limit } }),
    usage: (sessionId?: string, limit = 200) =>
      request<UsageRecord[]>("GET", "/api/usage", { query: { session_id: sessionId, limit } }),
    usageSummary: (days: number) => request<UsageSummary>("GET", "/api/usage/summary", { query: { days } }),
    commands: () => request<CommandInfo[]>("GET", "/api/commands"),
    latestSeq: () => request<LatestSeq>("GET", "/api/events/latest"),

    // harness
    harness: () => request<HarnessListing>("GET", "/api/harness"),
    harnessKind: (kind: string) => request<HarnessEntry[]>("GET", `/api/harness/${enc(kind)}`),
    reload: () => request<ReloadResult>("POST", "/api/harness/reload"),

    // approvals + questions
    pendingPermissions: (sessionId?: string) =>
      request<PendingPermission[]>("GET", "/api/permissions/pending", {
        query: { session_id: sessionId },
      }),
    replyPermission: (requestId: string, reply: PermissionReplyKind, message?: string | null) =>
      request<Record<string, boolean>>("POST", `/api/permissions/${enc(requestId)}/reply`, {
        body: { reply, message: message ?? null } satisfies PermissionReplyBody,
      }),
    pendingQuestions: (sessionId?: string) =>
      request<PendingQuestion[]>("GET", "/api/questions/pending", {
        query: { session_id: sessionId },
      }),
    answerQuestion: (requestId: string, responses: Record<string, unknown>[]) =>
      request<Record<string, boolean>>("POST", `/api/questions/${enc(requestId)}/answer`, {
        body: { responses },
      }),

    // memory
    memorySearch: (q: string, limit = 20) =>
      request<SearchHit[]>("GET", "/api/memory/search", { query: { q, limit } }),
    addFact: (body: FactIn) =>
      request<{ id: number; created: boolean }>("POST", "/api/memory/facts", { body }),

    // --- W0-B: doctor, audit, memory facts/files/curator, voice (web/sdk/ops2.ts)
    ...statusMethods(request, base, token),
    ...goalsApi(request), // --- W0-C (block at the end of this file)
    ...policyApi(request), // --- W1-C (block at the end of this file)
    ...meetingsApi(request, base, token), // --- F4 (block at the end of this file)

    subscribeEvents: (o: SubscribeOptions, onEvent: (e: ServerEvent) => void, h?: StreamHandlers) =>
      subscribeEvents({ ...o, baseUrl: base }, onEvent, h),
    connectControl: (o: Omit<ControlOptions, "baseUrl" | "token">) =>
      connectControl({ ...o, baseUrl: base, token: token() }),

    // --- W0-A: skills, plugins, cron, webhooks, deliveries, teams, kanban (./ops.ts)
    ...opsMethods(request),
    // --- end W0-A
  };
}

export type Client = ReturnType<typeof createClient>;

// =================================================================== SSE
export interface SubscribeOptions {
  readonly sessionId?: string | readonly string[];
  readonly afterSeq?: number;
  readonly follow?: boolean;
  readonly baseUrl?: string;
  /** Extra SSE event names beyond `EVENT_TYPES`. */
  readonly extraTypes?: readonly string[];
}

export interface StreamHandlers {
  readonly onOpen?: () => void;
  readonly onError?: (closed: boolean) => void;
}

/**
 * Subscribe to `GET /api/events`. EventSource reconnects on its own (server sends
 * `retry: 3000`) and re-sends `Last-Event-ID`, which the server prefers over `after_seq`.
 * If the browser gives up (readyState CLOSED, e.g. HTTP error), we reopen from the last
 * seen seq after 3s. Events are de-duplicated by `seq`. Returns an unsubscribe function.
 * Auth: EventSource cannot send headers and the server never accepts `?token=` here, so a
 * secured server is reached with the HttpOnly cookie set by `login()` (same origin).
 */
export function subscribeEvents(
  opts: SubscribeOptions,
  onEvent: (e: ServerEvent) => void,
  handlers: StreamHandlers = {},
): () => void {
  let lastSeq = opts.afterSeq ?? 0;
  let es: EventSource | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let stopped = false;
  const sessions =
    opts.sessionId === undefined
      ? []
      : typeof opts.sessionId === "string"
        ? [opts.sessionId]
        : [...opts.sessionId];

  const listener = (ev: MessageEvent<string>) => {
    let parsed: ServerEvent;
    try {
      parsed = JSON.parse(ev.data) as ServerEvent;
    } catch {
      return;
    }
    if (typeof parsed.seq !== "number" || parsed.seq <= lastSeq) return;
    lastSeq = parsed.seq;
    onEvent(parsed);
  };

  const open = () => {
    if (stopped) return;
    const q = buildQuery({
      after_seq: lastSeq,
      session_id: sessions,
      follow: opts.follow === false ? "false" : undefined,
    });
    es = new EventSource(`${opts.baseUrl ?? ""}/api/events${q}`);
    for (const t of [...EVENT_TYPES, ...(opts.extraTypes ?? [])])
      es.addEventListener(t, listener as EventListener);
    es.onopen = () => handlers.onOpen?.();
    es.onerror = () => {
      const closed = es?.readyState === EventSource.CLOSED;
      handlers.onError?.(closed);
      if (closed && opts.follow !== false) {
        es = null;
        timer = setTimeout(open, 3000);
      } else if (opts.follow === false) {
        es?.close(); // replay-only streams end on purpose
      }
    };
  };
  open();
  return () => {
    stopped = true;
    if (timer) clearTimeout(timer);
    es?.close();
  };
}

export * from "./control";
export * from "./events";
// --- W0-A
export * from "./ops";
// --- end W0-A
export type * from "./ops2"; // --- W0-B

// =================================================================== --- W0-C goals + suggestions
export type GoalInfo = S["GoalInfo"];
export type GoalCreate = S["GoalCreate"];
export type GoalPatch = S["GoalPatch"];
export type GoalStatus = GoalInfo["status"];
export type SuggestionInfo = S["SuggestionInfo"];
export type SuggestionCreate = S["SuggestionCreate"];
export type SuggestionProposed = S["SuggestionProposed"];
export type SuggestionAccepted = S["SuggestionAccepted"];
export type SuggestionStatus = SuggestionInfo["status"];

type RequestFn = <T>(method: string, path: string, init?: RequestInit2) => Promise<T>;

/** `/api/goals` + `/api/suggestions` (consent-first: nothing runs until `acceptSuggestion`). */
function goalsApi(request: RequestFn) {
  return {
    /** No `status` lists every goal. */
    listGoals: (status?: GoalStatus | "all", limit = 100) =>
      request<GoalInfo[]>("GET", "/api/goals", { query: { status, limit } }),
    createGoal: (body: Partial<GoalCreate> & Pick<GoalCreate, "title">) =>
      request<GoalInfo>("POST", "/api/goals", { body: { description: "", ...body } }),
    updateGoal: (id: number, body: Partial<GoalPatch>) =>
      request<GoalInfo>("PATCH", `/api/goals/${id}`, { body }),
    absorbGoal: (id: number, into: number) =>
      request<GoalInfo>("POST", `/api/goals/${id}/absorb`, { body: { into } }),
    listSuggestions: (status?: SuggestionStatus | "all", limit = 100) =>
      request<SuggestionInfo[]>("GET", "/api/suggestions", { query: { status, limit } }),
    proposeSuggestion: (body: Partial<SuggestionCreate> & Pick<SuggestionCreate, "title">) =>
      request<SuggestionProposed>("POST", "/api/suggestions", { body }),
    acceptSuggestion: (id: number) => request<SuggestionAccepted>("POST", `/api/suggestions/${id}/accept`),
    denySuggestion: (id: number) => request<SuggestionInfo>("POST", `/api/suggestions/${id}/deny`),
  };
}

// =================================================================== --- W1-C model/effort bandit
export type BanditStatus = S["BanditStatus"];
export type BanditArmInfo = S["BanditArmInfo"];
export type BanditArmStats = S["BanditArmStats"];
export type BanditOutcomeInfo = S["BanditOutcomeInfo"];
export type BanditResetResult = S["BanditResetResult"];

/** Payload of the `policy.choice` session event (src/auoreo/schema/policy.py PolicyChoiceData). */
export interface PolicyChoiceData {
  run_id: string;
  context_key: string;
  arm: BanditArmInfo;
  explore: boolean;
  why: string;
  epsilon: number;
}

/** `/api/policy/bandit`: status (settings, arms, learned stats, recent outcomes) and reset. */
function policyApi(request: RequestFn) {
  return {
    banditStatus: () => request<BanditStatus>("GET", "/api/policy/bandit"),
    /** No `context` resets every context. Settings are unchanged. */
    resetBandit: (context?: string) =>
      request<BanditResetResult>("POST", "/api/policy/bandit/reset", { body: { context: context ?? null } }),
  };
}

// =================================================================== --- F4 meetings
export type MeetingInfo = S["MeetingInfo"];
export type MeetingDetail = S["MeetingDetail"];
export type MeetingSegment = S["MeetingSegment"];
export type MeetingStatus = MeetingInfo["status"];
export type MeetingStartRequest = S["MeetingStartRequest"];
export type MeetingChunkInfo = S["MeetingChunkInfo"];
export type MeetingSummarizeStarted = S["MeetingSummarizeStarted"];

/** `/api/voice/meetings`: start → upload chunks (or one recording) → stop (finalise) → summarize. */
function meetingsApi(request: RequestFn, base: string, token: () => string | null) {
  return {
    listMeetings: () => request<MeetingInfo[]>("GET", "/api/voice/meetings"),
    getMeeting: (id: string) => request<MeetingDetail>("GET", `/api/voice/meetings/${enc(id)}`),
    startMeeting: (body: Partial<MeetingStartRequest> = {}) =>
      request<{ meeting_id: string }>("POST", "/api/voice/meetings", { body }),
    /** Multipart upload; chunks are appended in order (a full recording works too). */
    uploadMeetingAudio: async (id: string, audio: Blob, filename = "chunk.wav") => {
      const form = new FormData();
      form.append("file", audio, filename);
      const headers: Record<string, string> = { Accept: "application/json" };
      const t = token();
      if (t) headers.Authorization = `Bearer ${t}`;
      const res = await fetch(`${base}/api/voice/meetings/${enc(id)}/audio`, {
        method: "POST",
        body: form,
        headers,
      });
      if (!res.ok) {
        let detail: unknown = res.statusText;
        try {
          detail = ((await res.json()) as { detail?: unknown }).detail ?? detail;
        } catch {
          /* non-JSON error body */
        }
        throw new ApiError(res.status, detail);
      }
      return (await res.json()) as MeetingChunkInfo;
    },
    attachMeetingPeer: (id: string, rtcId: string) =>
      request<MeetingInfo>("POST", `/api/voice/meetings/${enc(id)}/attach`, { body: { rtc_id: rtcId } }),
    stopMeeting: (id: string) => request<MeetingDetail>("POST", `/api/voice/meetings/${enc(id)}/stop`),
    summarizeMeeting: (id: string) =>
      request<MeetingSummarizeStarted>("POST", `/api/voice/meetings/${enc(id)}/summarize`),
  };
}
