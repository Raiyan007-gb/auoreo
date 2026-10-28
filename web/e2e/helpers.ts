import { mkdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import type { APIRequestContext, Page } from "@playwright/test";

const here = dirname(fileURLToPath(import.meta.url));
/** Screenshots land in <repo>/.artifacts/ui/ (gitignored). */
export const SHOTS = resolve(here, "..", "..", ".artifacts", "ui");
mkdirSync(SHOTS, { recursive: true });

export async function shot(page: Page, name: string): Promise<void> {
  await page.screenshot({ path: join(SHOTS, `${name}.png`), fullPage: false });
}

export interface Session {
  id: string;
  title: string;
}

export async function createSession(request: APIRequestContext, title: string): Promise<Session> {
  const res = await request.post("/api/sessions", { data: { title } });
  if (!res.ok()) throw new Error(`create session failed: ${res.status()}`);
  return (await res.json()) as Session;
}

export async function createUntitled(request: APIRequestContext): Promise<Session> {
  const res = await request.post("/api/sessions", { data: {} });
  if (!res.ok()) throw new Error(`create session failed: ${res.status()}`);
  return (await res.json()) as Session;
}

/** Post a chat turn over REST and wait until the session is idle with an assistant reply. */
export async function chatAndWait(request: APIRequestContext, id: string, message: string): Promise<void> {
  const res = await request.post("/api/chat", { data: { session_id: id, message } });
  if (res.status() !== 202) throw new Error(`chat failed: ${res.status()}`);
  const deadline = Date.now() + 20_000;
  while (Date.now() < deadline) {
    const s = (await (await request.get(`/api/sessions/${id}`)).json()) as { status: string };
    const msgs = (await (await request.get(`/api/sessions/${id}/messages`)).json()) as { role: string }[];
    if (s.status === "idle" && msgs.some((m) => m.role === "assistant")) return;
    await new Promise((r) => setTimeout(r, 150));
  }
  throw new Error(`session ${id} did not go idle`);
}

/** Open the session header's "More actions" menu (fork, compact, export, archive, delete). */
export async function openMore(page: Page): Promise<void> {
  await page.getByTestId("session-more").click();
  await page.getByRole("menu").waitFor();
}

export async function send(page: Page, text: string): Promise<void> {
  const box = page.getByTestId("composer");
  await box.fill(text);
  await box.press("Enter");
}
