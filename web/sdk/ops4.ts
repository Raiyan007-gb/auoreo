/**
 * G6 surface of the client: PREFS.md proposals (agent proposes, human approves/denies) and
 * skills-hub sources. Spread into `createClient()`; every wire type aliases `types.gen.ts`.
 */
import type { components } from "./types.gen";

type S = components["schemas"];
export type PrefsProposal = S["PrefsProposal"];
export type PrefsProposalIn = S["PrefsProposalIn"];
export type SkillSources = S["SkillSources"];
export type HubInstalledSkill = S["HubInstalledSkill"];

type Ops4Request = <T>(method: string, path: string, init?: { body?: unknown }) => Promise<T>;

const enc = encodeURIComponent;

export function g6Methods(request: Ops4Request) {
  return {
    prefsProposals: (status?: PrefsProposal["status"]) =>
      request<PrefsProposal[]>("GET", `/api/memory/prefs/proposals${status ? `?status=${enc(status)}` : ""}`),
    proposePref: (body: PrefsProposalIn) => request<PrefsProposal>("POST", "/api/memory/prefs/proposals", { body }),
    /** Applies the proposal to PREFS.md; the `prefs.approved` event seq is recorded in the entry. */
    approvePref: (id: string) => request<PrefsProposal>("POST", `/api/memory/prefs/proposals/${enc(id)}/approve`),
    denyPref: (id: string, reason = "") =>
      request<PrefsProposal>("POST", `/api/memory/prefs/proposals/${enc(id)}/deny`, { body: { reason } }),
    skillSources: () => request<SkillSources>("GET", "/api/skills/sources"),
    approveHubSkill: (name: string) => request<HubInstalledSkill>("POST", `/api/skills/hub/${enc(name)}/approve`),
  };
}
