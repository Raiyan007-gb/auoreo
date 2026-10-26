/**
 * I5 surface of the client: read-only share links (+ the public view), the skills hub
 * (search / install into quarantine / remove), the Memory usage gauge and in-process LSP
 * stop. Spread into `createClient()`; every wire type aliases `types.gen.ts`.
 */
import type { components } from "./types.gen";

type S = components["schemas"];
export type ShareCreated = S["ShareCreated"];
export type SharesRevoked = S["SharesRevoked"];
export type SharedSession = S["SharedSession"];
export type HubSearchResult = S["HubSearchResult"];
export type HubSearchHit = S["HubSearchHit"];
export type HubRemoved = S["HubRemoved"];
export type MemoryUsage = S["MemoryUsage"];
export type LspStopped = S["LspStopped"];

type Ops6Request = <T>(method: string, path: string, init?: { body?: unknown; query?: Record<string, string> }) => Promise<T>;

const enc = encodeURIComponent;

export function i5Methods(request: Ops6Request) {
  return {
    /** The token is shown once; the server keeps only its hash. */
    createShare: (sessionId: string) => request<ShareCreated>("POST", `/api/sessions/${enc(sessionId)}/share`),
    revokeShares: (sessionId: string) => request<SharesRevoked>("DELETE", `/api/sessions/${enc(sessionId)}/share`),
    /** Public: no auth; the token is the capability. Messages are redacted server-side. */
    sharedSession: (token: string) => request<SharedSession>("GET", `/api/shared/${enc(token)}`),
    hubSearch: (q = "") => request<HubSearchResult>("GET", "/api/skills/hub/search", { query: { q } }),
    /** Lands quarantined (invisible to agents) until `approveHubSkill`. */
    hubInstall: (name: string, tap?: string) =>
      request<S["HubInstalledSkill"]>("POST", "/api/skills/hub/install", { body: { name, tap: tap ?? null, force: false } }),
    hubRemove: (name: string) => request<HubRemoved>("DELETE", `/api/skills/hub/${enc(name)}`),
    memoryUsage: () => request<MemoryUsage>("GET", "/api/memory/usage"),
    lspStop: (body: { session_id?: string; language?: string } = {}) =>
      request<LspStopped>("POST", "/api/lsp/stop", { body }),
  };
}
