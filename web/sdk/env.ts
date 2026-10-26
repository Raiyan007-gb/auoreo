/**
 * Settings → Environment surface of the client (`/api/env`): the variable catalog with effective
 * values, live-applied setting writes, write-only secrets, custom variables, undo and the
 * `~/.auoreo/.env` import. No response ever carries a secret value. Spread into `createClient()`;
 * every wire type aliases `types.gen.ts`.
 */
import type { components } from "./types.gen";

type S = components["schemas"];
export type EnvListing = S["EnvListing"];
export type EnvEntry = S["EnvEntry"];
export type EnvSection = S["EnvSectionOut"];
export type EnvApplied = S["EnvApplied"];
export type CustomVar = S["CustomVarOut"];
export type CustomVarWrite = S["CustomVarWrite"];
export type DotenvImport = S["DotenvImport"];

type EnvRequest = <T>(method: string, path: string, init?: { body?: unknown }) => Promise<T>;

const enc = encodeURIComponent;

export function envMethods(request: EnvRequest) {
  return {
    envList: () => request<EnvListing>("GET", "/api/env"),
    /** `value: null` resets to the default. 409 = set in the real environment, 403 = env-only. */
    envSet: (name: string, value: unknown) =>
      request<EnvApplied>("PUT", `/api/env/settings/${enc(name)}`, { body: { value } }),
    /** Write-only: the value is never returned. */
    envSecretSet: (name: string, value: string) =>
      request<EnvApplied>("PUT", `/api/env/secrets/${enc(name)}`, { body: { value } }),
    envSecretRotate: (name: string, value: string, graceS?: number) =>
      request<EnvApplied>("POST", `/api/env/secrets/${enc(name)}/rotate`, { body: { value, grace_s: graceS ?? null } }),
    envSecretDelete: (name: string) => request<EnvApplied>("DELETE", `/api/env/secrets/${enc(name)}`),
    envCustomCreate: (body: CustomVarWrite) => request<EnvApplied>("POST", "/api/env/custom", { body }),
    /** Omit `value` to keep it. */
    envCustomUpdate: (body: CustomVarWrite) =>
      request<EnvApplied>("PUT", `/api/env/custom/${enc(body.name)}`, { body }),
    envCustomDelete: (name: string) => request<EnvApplied>("DELETE", `/api/env/custom/${enc(name)}`),
    envUndo: () => request<EnvApplied>("POST", "/api/env/undo"),
    envImportDotenv: (dryRun: boolean) =>
      request<DotenvImport>("POST", "/api/env/import-dotenv", { body: { dry_run: dryRun } }),
  };
}
