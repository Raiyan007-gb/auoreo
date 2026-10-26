/**
 * G7 surface of the client: the session plan, per-session MCP/plugin toggles and usage
 * charts. Spread into `createClient()`; every wire type aliases `types.gen.ts`.
 */
import type { components } from "./types.gen";

type S = components["schemas"];
export type SessionPlan = S["SessionPlan"];
export type SessionSources = S["SessionSources"];
export type SessionSource = S["SessionSource"];
export type UsageSeries = S["UsageSeries"];
export type UsagePoint = S["UsagePoint"];

type Ops5Request = <T>(method: string, path: string, init?: { body?: unknown }) => Promise<T>;

const enc = encodeURIComponent;

export function g7Methods(request: Ops5Request) {
  return {
    sessionPlan: (id: string) => request<SessionPlan>("GET", `/api/sessions/${enc(id)}/plan`),
    sessionSources: (id: string) => request<SessionSources>("GET", `/api/sessions/${enc(id)}/sources`),
    /** Replaces the session's disabled list (`mcp:<server>` / `plugin:<name>`); the next turn honours it. */
    setSessionSources: (id: string, disabled: string[]) =>
      request<SessionSources>("PUT", `/api/sessions/${enc(id)}/sources`, { body: { disabled } }),
    usageTimeseries: (days = 7, bucket?: "hour" | "day") =>
      request<UsageSeries>("GET", `/api/usage/timeseries?days=${days}${bucket ? `&bucket=${bucket}` : ""}`),
  };
}
