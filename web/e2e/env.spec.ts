import { writeFileSync } from "node:fs";
import { expect, type APIRequestContext, type Page, test } from "@playwright/test";
import { chatAndWait, createSession, shot } from "./helpers";

/** Settings → Environment (ENV): live settings, write-only secrets, custom variables, env-only
 * lock, `.env` import. Every test restores what it changed (one server serves the suite). */

const SECRET = "e2e-secret-value-0123456789abcdef";

async function openEnv(page: Page) {
  await page.goto("/settings#environment");
  await expect(page.getByTestId("env-panel")).toBeVisible();
}

async function envJson(request: APIRequestContext) {
  return (await (await request.get("/api/env")).json()) as {
    sections: { entries: { name: string; source: string; is_set: boolean; writable: boolean }[] }[];
    dotenv_path: string;
  };
}

test("a live setting applies without a restart (teleop flag → /api/rtc/capabilities)", async ({ page, request }) => {
  await openEnv(page);
  const caps = async () =>
    ((await (await request.get("/api/rtc/capabilities")).json()) as { teleop_input_enabled: boolean })
      .teleop_input_enabled;
  expect(await caps()).toBe(false);
  await page.getByTestId("env-search").fill("TELEOP");
  const toggle = page.getByTestId("env-input-AUOREO_TELEOP_INPUT_ENABLED");
  await expect(toggle).toHaveAttribute("aria-checked", "false");
  await toggle.click();
  await expect(page.getByTestId("env-toast")).toContainText("Applied");
  await expect(toggle).toHaveAttribute("aria-checked", "true");
  await expect(page.getByTestId("env-source-AUOREO_TELEOP_INPUT_ENABLED")).toHaveText("database");
  expect(await caps()).toBe(true);
  await shot(page, "env-live-setting");
  await page.getByTestId("env-reset-AUOREO_TELEOP_INPUT_ENABLED").click();
  await expect(toggle).toHaveAttribute("aria-checked", "false");
  expect(await caps()).toBe(false);
});

test("secrets are write-only: set, rotate, delete — the value is never rendered", async ({ page }) => {
  await openEnv(page);
  await page.getByTestId("env-search").fill("EXA_API_KEY");
  const input = page.getByTestId("env-input-EXA_API_KEY");
  await expect(input).toHaveAttribute("placeholder", "not set");
  await input.fill(SECRET);
  await page.getByTestId("env-save-EXA_API_KEY").click();
  await expect(page.getByTestId("env-toast")).toContainText("Applied");
  await expect(input).toHaveAttribute("placeholder", "•••••• (set)");
  await expect(input).toHaveValue("");
  await expect(page.getByTestId("env-row-EXA_API_KEY")).toContainText("updated");
  await input.fill(`${SECRET}-2`);
  await page.getByTestId("env-rotate-EXA_API_KEY").click();
  await expect(input).toHaveValue(""); // cleared once the write landed
  await expect(page.getByTestId("env-toast")).toContainText("Applied");
  expect(await page.content()).not.toContain(SECRET);
  await shot(page, "env-secret-set");
  await page.getByTestId("env-delete-EXA_API_KEY").click();
  await expect(input).toHaveAttribute("placeholder", "not set");
  expect(await page.content()).not.toContain(SECRET);
});

test("a custom variable reaches the agent's next turn (names only for secrets)", async ({ page, request }) => {
  await openEnv(page);
  await page.getByPlaceholder("MY_VARIABLE").fill("E2E_PLAIN_VAR");
  await page.getByPlaceholder("value", { exact: true }).fill("plain-e2e-value");
  await page.getByTestId("custom-new-expose_to_agent").check();
  await page.getByTestId("custom-add").click();
  await expect(page.getByTestId("custom-row-E2E_PLAIN_VAR")).toBeVisible();
  await page.getByPlaceholder("MY_VARIABLE").fill("E2E_SECRET_VAR");
  await page.getByTestId("custom-new-secret").check();
  await page.getByPlaceholder("value", { exact: true }).fill(SECRET);
  await page.getByTestId("custom-new-expose_to_shell").check();
  await page.getByTestId("custom-new-expose_to_agent").check();
  await page.getByTestId("custom-add").click();
  await expect(page.getByTestId("custom-row-E2E_SECRET_VAR")).toContainText("•••••• (set)");
  expect(await page.content()).not.toContain(SECRET);
  await shot(page, "env-custom-vars");

  const s = await createSession(request, "e2e env context");
  await chatAndWait(request, s.id, "/context");
  const msgs = (await (await request.get(`/api/sessions/${s.id}/messages`)).json()) as {
    role: string;
    content: string;
  }[];
  const reply = msgs.filter((m) => m.role === "assistant").at(-1)?.content ?? "";
  expect(reply).toContain("E2E_PLAIN_VAR = plain-e2e-value");
  expect(reply).toContain("E2E_SECRET_VAR (secret; value hidden");
  expect(reply).not.toContain(SECRET);

  await page.getByTestId("custom-delete-E2E_PLAIN_VAR").click();
  await page.getByTestId("custom-delete-E2E_SECRET_VAR").click();
  await expect(page.getByTestId("custom-list")).toHaveCount(0);
});

test("env-only security knobs are read-only", async ({ page, request }) => {
  await openEnv(page);
  await page.getByTestId("env-search").fill("AUOREO_PERMISSION_MODE");
  const row = page.getByTestId("env-row-AUOREO_PERMISSION_MODE");
  await expect(row.getByTestId("env-locked-AUOREO_PERMISSION_MODE")).toContainText("security knob");
  await expect(row.locator("input, select, textarea")).toHaveCount(0);
  await shot(page, "env-env-only");
  const res = await request.put("/api/env/settings/AUOREO_PERMISSION_MODE", { data: { value: "yolo" } });
  expect(res.status()).toBe(403);
});

test("import existing .env moves keys and settings, skips security knobs", async ({ page, request }) => {
  const { dotenv_path } = await envJson(request);
  writeFileSync(dotenv_path, `TAVILY_API_KEY=${SECRET}\nAUOREO_LOCALE=pt-BR\nAUOREO_YOLO=1\n`);
  await openEnv(page);
  await page.getByTestId("env-import").click();
  const dialog = page.getByTestId("import-dialog");
  await expect(dialog).toContainText("TAVILY_API_KEY");
  await expect(dialog).toContainText("AUOREO_YOLO");
  await shot(page, "env-import-dialog");
  await page.getByTestId("import-confirm").click();
  await expect(page.getByTestId("env-toast")).toContainText("Moved 2");
  const entries = (await envJson(request)).sections.flatMap((s) => s.entries);
  expect(entries.find((e) => e.name === "TAVILY_API_KEY")?.source).toBe("secrets");
  expect(entries.find((e) => e.name === "AUOREO_LOCALE")?.source).toBe("db");
  expect(await page.content()).not.toContain(SECRET);
  // clean up: undo the settings write, drop the key and the file
  await page.getByTestId("env-undo").click();
  await expect(page.getByTestId("env-toast")).toContainText(/Applied|nothing/);
  expect((await request.delete("/api/env/secrets/TAVILY_API_KEY")).ok()).toBe(true);
  writeFileSync(dotenv_path, "");
});
