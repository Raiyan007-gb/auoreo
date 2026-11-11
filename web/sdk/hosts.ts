/**
 * M7 SSH host inventory surface of the client (`/api/hosts`, 404 unless `m7.ssh.web`): list,
 * edit, delete and check hosts. Fingerprints and state only: no request or response carries a
 * private key, a public key body or a secret value, and nothing here trusts a host key or
 * generates, deploys or rotates a key (those are `auoreo ssh …` at the CLI). Spread into
 * `createClient()`; every wire type aliases `types.gen.ts`.
 */
import type { components, operations } from "./types.gen";

type S = components["schemas"];
export type HostInfo = S["HostInfo"];
export type HostList = S["HostList"];
export type HostCheck = S["HostCheck"];
export type HostWrite = operations["put_host_api_hosts__name__put"]["requestBody"]["content"]["application/json"];

type HostsRequest = <T>(method: string, path: string, init?: { body?: unknown }) => Promise<T>;

const enc = encodeURIComponent;

export function hostsMethods(request: HostsRequest) {
  return {
    hosts: () => request<HostList>("GET", "/api/hosts"),
    /** A new address or port is untrusted until `auoreo ssh test <name>`; 403 over the tunnel. */
    hostPut: (name: string, body: HostWrite) => request<HostInfo>("PUT", `/api/hosts/${enc(name)}`, { body }),
    /** Deletes the host file only (never the stored key). */
    hostDelete: (name: string) => request<void>("DELETE", `/api/hosts/${enc(name)}`),
    /** Connects with the stored key and the trusted host key; never trusts an unknown key. */
    hostCheck: (name: string) => request<HostCheck>("POST", `/api/hosts/${enc(name)}/check`),
  };
}
