/**
 * Settings → Models surface of the client (`/api/providers`, `/api/models/selection`): detect a
 * provider from a key, connect it (the key is validated by listing the provider's models and is
 * write-only: no response ever carries it), browse the cached model list, choose the main / small /
 * embedding model (applied live). Spread into `createClient()`; wire types alias `types.gen.ts`.
 */
import type { components } from "./types.gen";

type S = components["schemas"];
export type ProviderSpec = S["ProviderSpecOut"];
export type ProviderDetect = S["ProviderDetectResult"];
export type ProviderCreate = S["ProviderCreate"];
export type ProviderUpdate = S["ProviderUpdate"];
export type ProviderConnection = S["ProviderConnectionOut"];
export type ProviderList = S["ProviderList"];
export type ProviderResult = S["ProviderResult"];
export type ProviderModel = S["ProviderModelOut"];
export type ProviderModels = S["ProviderModels"];
export type ModelRef = S["ModelRef"];
export type ModelSelection = S["ModelSelection"];
export type SelectedModel = S["SelectedModel"];
export type ModelSelectionWrite = S["ModelSelectionWrite"];
export type ModelSelectionApplied = S["ModelSelectionApplied"];
export type ModelRole = SelectedModel["role"];

type Req = <T>(
  method: string,
  path: string,
  init?: { body?: unknown; query?: Record<string, string | number | boolean | undefined> },
) => Promise<T>;

export function modelsMethods(request: Req) {
  return {
    /** Which provider a key looks like (never stored). */
    providerDetect: (key: string) => request<ProviderDetect>("POST", "/api/providers/detect", { body: { key } }),
    providers: () => request<ProviderList>("GET", "/api/providers"),
    /** Validates first; `ok: false` carries the provider's error and nothing was stored. */
    providerAdd: (body: ProviderCreate) => request<ProviderResult>("POST", "/api/providers", { body }),
    providerUpdate: (id: number, body: ProviderUpdate) =>
      request<ProviderResult>("PUT", `/api/providers/${id}`, { body }),
    providerRefresh: (id: number) => request<ProviderResult>("POST", `/api/providers/${id}/refresh`),
    /** 409 while it serves the selected model unless `force`. */
    providerDelete: (id: number, force = false) =>
      request<void>("DELETE", `/api/providers/${id}`, { query: force ? { force: 1 } : undefined }),
    providerModels: (id: number, opts: { kind?: ProviderModel["kind"]; q?: string; refresh?: boolean } = {}) =>
      request<ProviderModels>("GET", `/api/providers/${id}/models`, {
        query: { kind: opts.kind, q: opts.q || undefined, refresh: opts.refresh ? 1 : undefined },
      }),
    modelSelection: () => request<ModelSelection>("GET", "/api/models/selection"),
    modelSelect: (body: ModelSelectionWrite) =>
      request<ModelSelectionApplied>("PUT", "/api/models/selection", { body }),
  };
}
