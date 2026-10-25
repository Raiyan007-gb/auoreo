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

export async function send(page: Page, text: string): Promise<void> {
  const box = page.getByTestId("composer");
  await box.fill(text);
  await box.press("Enter");
}
