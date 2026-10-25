/**
 * Ops surface of the client (W0-A): skills, plugins, cron jobs, webhooks, deliveries, teams
 * and kanban. Spread into `createClient()` so `client.ts` stays small; every wire type is an
 * alias of the generated `types.gen.ts`.
 */
import type { components, paths } from "./types.gen";

type S = components["schemas"];
export type CronJobInfo = S["CronJobInfo"];
export type CronRunInfo = S["CronRunInfo"];
export type CronRunAccepted = S["CronRunAccepted"];
export type CronPaused = S["CronPaused"];
export type DeliveryInfo = S["DeliveryInfo"];
export type DeliveryReplay = S["DeliveryReplay"];
export type PluginInfo = S["PluginInfo"];
export type PluginStatus = PluginInfo["status"];
export type PluginSkillInfo = S["PluginSkillInfo"];
export type PluginPaired = S["PluginPaired"];
export type WebhookRouteInfo = S["WebhookRouteInfo"];
export type TeamInfo = S["TeamInfo"];
export type TeamDetail = S["TeamDetail"];
export type TeamMemberInfo = S["TeamMemberInfo"];
export type MailMessage = S["MailMessage"];
export type KanbanTask = S["KanbanTask"];
export type KanbanStatus = KanbanTask["status"];
export type KanbanCreate = S["KanbanCreate"];
/** `GET /api/deliveries` query (status / kind / limit). */
export type DeliveryQuery = NonNullable<paths["/api/deliveries"]["get"]["parameters"]["query"]>;

export type PluginAction = "start" | "stop" | "reload";
/** Response model of each `POST /api/cron/{name}/{action}`. */
export interface CronActionResult {
  run: CronRunAccepted;
  pause: CronPaused;
  resume: CronPaused;
}

type OpsRequest = <T>(
  method: string,
  path: string,
  init?: { query?: Record<string, string | number | boolean | undefined | null>; body?: unknown },
) => Promise<T>;

const enc = encodeURIComponent;

export function opsMethods(request: OpsRequest) {
  return {
    /** Skills as harness rows (`GET /api/harness/skills`). */
    skills: () => request<S["HarnessEntry"][]>("GET", "/api/harness/skills"),

    plugins: () => request<PluginInfo[]>("GET", "/api/plugins"),
    pluginAction: (name: string, action: PluginAction) =>
      request<PluginInfo>("POST", `/api/plugins/${enc(name)}/${action}`),
    pluginSkills: (name: string) => request<PluginSkillInfo[]>("GET", `/api/plugins/${enc(name)}/skills`),
    pairPlugin: (name: string, code: string) =>
      request<PluginPaired>("POST", `/api/plugins/${enc(name)}/pair`, { body: { code } satisfies S["PluginPair"] }),

    cron: () => request<CronJobInfo[]>("GET", "/api/cron"),
    cronAction: <A extends keyof CronActionResult>(name: string, action: A) =>
      request<CronActionResult[A]>("POST", `/api/cron/${enc(name)}/${action}`),
    cronRuns: (name: string, limit = 20) =>
      request<CronRunInfo[]>("GET", `/api/cron/${enc(name)}/runs`, { query: { limit } }),

    webhooks: () => request<WebhookRouteInfo[]>("GET", "/api/webhooks"),
    deliveries: (query: DeliveryQuery = {}) => request<DeliveryInfo[]>("GET", "/api/deliveries", { query }),
    /** `dryRun` (the server default) only returns the stored prompt; false queues a new row. */
    replayDelivery: (id: number, dryRun = true) =>
      request<DeliveryReplay>("POST", `/api/deliveries/${id}/replay`, { query: { dry_run: dryRun } }),

    teams: () => request<TeamInfo[]>("GET", "/api/teams"),
    team: (id: string) => request<TeamDetail>("GET", `/api/teams/${enc(id)}`),
    mailbox: (id: string, member?: string) =>
      request<MailMessage[]>("GET", `/api/teams/${enc(id)}/mailbox`, { query: { member } }),

    kanban: (board?: string, status?: KanbanStatus) =>
      request<KanbanTask[]>("GET", "/api/kanban", { query: { board, status } }),
    createKanban: (body: { title: string; body?: string; board?: string }) =>
      request<KanbanTask>("POST", "/api/kanban", {
        body: { board: "default", body: "", ...body } satisfies KanbanCreate,
      }),
  };
}
