/**
 * Ops methods (W0-B): doctor, audit log, memory facts/files/curator, voice.
 * Spread into `createClient()` so `client.ts` stays small; same `request<T>` and auth.
 */
import type { components } from "./types.gen";

type S = components["schemas"];
export type DoctorCheck = S["DoctorCheck"];
export type DoctorReport = S["DoctorReport"];
export type DoctorFix = S["DoctorFix"];
export type DoctorFixPlan = S["DoctorFixPlan"];
export type DoctorFixResult = S["DoctorFixResult"];
export type AuditEntry = S["AuditEntry"];
export type AuditPage = S["AuditPage"];
export type AuditVerify = S["AuditVerify"];
export type MemoryFact = S["MemoryFact"];
export type MemoryFactPage = S["MemoryFactPage"];
export type MemoryFile = S["MemoryFile"];
export type MemoryFiles = S["MemoryFiles"];
export type FactActionResult = S["FactActionResult"];
export type CuratorReport = S["CuratorReport"];
export type CuratorReportText = S["CuratorReportText"];
export type VoiceStatus = S["VoiceStatus"];
export type VoiceTranscription = S["VoiceTranscription"];
export type WelcomeEvent = S["WelcomeEvent"];
export type OpenAIModelList = S["OpenAIModelList"];

type Query = Record<string, string | number | boolean | undefined | null>;
type Request = <T>(
  method: string,
  path: string,
  init?: { query?: Query; body?: unknown; raw?: boolean; headers?: Record<string, string> },
) => Promise<T>;

export interface AuditQuery {
  readonly after_seq?: number;
  readonly before_seq?: number;
  readonly limit?: number;
  readonly session_id?: string;
  readonly action?: string;
  readonly newest_first?: boolean;
}

export interface FactQuery {
  readonly query?: string;
  readonly include_archived?: boolean;
  readonly pinned?: boolean;
  readonly limit?: number;
  readonly offset?: number;
}

const enc = encodeURIComponent;

export function statusMethods(request: Request, base: string, token: () => string | null) {
  return {
    doctor: () => request<DoctorReport>("GET", "/api/doctor"),
    doctorFixes: () => request<DoctorFixPlan>("GET", "/api/doctor/fixes"),
    applyDoctorFixes: () => request<DoctorFixResult>("POST", "/api/doctor/fixes"),
    audit: (q: AuditQuery = {}) => request<AuditPage>("GET", "/api/audit", { query: { ...q } }),
    auditVerify: () => request<AuditVerify>("GET", "/api/audit/verify"),

    memoryFacts: (q: FactQuery = {}) => request<MemoryFactPage>("GET", "/api/memory/facts", { query: { ...q } }),
    memoryFiles: () => request<MemoryFiles>("GET", "/api/memory/files"),
    forgetFact: (id: number) => request<FactActionResult>("POST", `/api/memory/facts/${enc(String(id))}/forget`),
    pinFact: (id: number, pinned: boolean) =>
      request<FactActionResult>("POST", `/api/memory/facts/${enc(String(id))}/pin`, { body: { pinned } }),
    factFeedback: (id: number, helpful: boolean) =>
      request<FactActionResult>("POST", `/api/memory/facts/${enc(String(id))}/feedback`, { body: { helpful } }),
    curatorRun: (dryRun: boolean, force = true) =>
      request<CuratorReport>("POST", "/api/memory/curator/run", { query: { dry_run: dryRun, force } }),
    curatorReport: () => request<CuratorReportText>("GET", "/api/memory/curator/report"),

    /** Audit an onboarding step outcome (`welcome.<action>`). */
    welcomeEvent: (body: WelcomeEvent) => request<void>("POST", "/api/welcome/event", { body }),
    /** OpenAI-compatible model ids (routing groups, `auoreo/<group>`, aliases). */
    models: () => request<OpenAIModelList>("GET", "/v1/models"),

    voiceStatus: () => request<VoiceStatus>("GET", "/api/voice/status"),
    /** Multipart upload; with `sessionId` the server also submits the transcript as a followup. */
    transcribe: async (audio: Blob, sessionId?: string, filename = "speech.webm") => {
      const form = new FormData();
      form.append("file", audio, filename);
      if (sessionId) form.append("session_id", sessionId);
      const headers: Record<string, string> = { Accept: "application/json" };
      const t = token();
      if (t) headers.Authorization = `Bearer ${t}`;
      const res = await fetch(`${base}/api/voice/transcribe`, { method: "POST", body: form, headers });
      if (!res.ok) {
        let detail: unknown = res.statusText;
        try {
          detail = ((await res.json()) as { detail?: unknown }).detail ?? detail;
        } catch {
          /* non-JSON error body */
        }
        throw new Error(typeof detail === "string" ? detail : `HTTP ${res.status}`);
      }
      return (await res.json()) as VoiceTranscription;
    },
    /** Audio bytes for `text` (the backend's native format). */
    speak: async (text: string, sessionId?: string) => {
      const res = await request<Response>("POST", "/api/voice/speak", {
        body: { text: text.slice(0, 5000), session_id: sessionId ?? null },
        raw: true,
      });
      return res.blob();
    },
  };
}
