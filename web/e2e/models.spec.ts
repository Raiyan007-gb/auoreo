import { expect, type APIRequestContext, type Page, test } from "@playwright/test";
import { chatAndWait, createSession, shot } from "./helpers";

/** Settings → Models: paste a key → detect → connect (validated against the local fake provider that
 * `run.mjs` serves) → list models → use as main → the next chat turn runs on it, no restart; a bad
 * key shows the provider's error and stores nothing; remove the provider. Restores what it changed. */

const GOOD = "sk-proj-e2e-good-key-0123456789abcdef"; // run.mjs → startProvider()
const BAD = "sk-proj-e2e-wrong-key-9876543210fedcba";

async function openModels(page: Page) {
  await page.goto("/settings#models");
  await expect(page.getByTestId("models-panel")).toBeVisible();
}

async function connections(request: APIRequestContext) {
  return ((await (await request.get("/api/providers")).json()) as { connections: { id: number; provider: string }[] })
    .connections;
}

async function cleanup(request: APIRequestContext) {
  for (const c of await connections(request)) await request.delete(`/api/providers/${c.id}?force=1`);
  await request.put("/api/env/settings/AUOREO_MODEL", { data: { value: null } });
  await request.put("/api/env/settings/AUOREO_SMALL_MODEL", { data: { value: null } });
}

test.afterEach(async ({ request }) => cleanup(request));

test("a bad key shows the provider's error and stores nothing", async ({ page, request }) => {
  await openModels(page);
  await page.getByTestId("provider-key").fill(BAD);
  await expect(page.getByTestId("provider-detected")).toContainText("OpenAI");
  await expect(page.getByTestId("provider-select")).toHaveValue("openai");
  await page.getByTestId("provider-connect").click();
  await expect(page.getByTestId("provider-error")).toContainText("Incorrect API key");
  await shot(page, "models-bad-key");
  expect(await connections(request)).toEqual([]);
  const env = (await (await request.get("/api/env")).json()) as {
    sections: { entries: { name: string; is_set: boolean }[] }[];
  };
  const openai = env.sections.flatMap((s) => s.entries).find((e) => e.name === "OPENAI_API_KEY");
  expect(openai?.is_set).toBe(false);
  await expect(page.getByTestId("provider-key")).toHaveAttribute("type", "password"); // kept only to fix a typo
});

test("ambiguous keys ask for the provider", async ({ page }) => {
  await openModels(page);
  await page.getByTestId("provider-key").fill("sk-0123456789abcdef0123456789abcdef");
  await expect(page.getByTestId("provider-detected")).toContainText("Could be OpenAI, DeepSeek");
  await expect(page.getByTestId("provider-select")).toHaveValue("");
  await expect(page.getByTestId("provider-connect")).toBeDisabled();
});

test("connect → list models → use as main → the next turn uses it → remove", async ({ page, request }) => {
  await openModels(page);
  await page.getByTestId("provider-key").fill(GOOD);
  await expect(page.getByTestId("provider-detected")).toContainText("Detected: OpenAI");
  await page.getByTestId("provider-connect").click();
  await expect(page.getByTestId("models-toast")).toContainText("OpenAI connected — 3 models");
  await expect(page.getByTestId("provider-status-openai")).toHaveAttribute("data-status", "ok");
  await expect(page.getByTestId("provider-count-openai")).toHaveText("3 models");
  await expect(page.getByTestId("provider-key")).toHaveValue("");
  expect(await page.content()).not.toContain(GOOD);

  await page.getByTestId("model-search").fill("alpha");
  await expect(page.getByTestId("model-list").locator("li")).toHaveCount(1);
  await shot(page, "models-connected");
  await page.getByTestId("use-main-e2e-model-alpha").click();
  await expect(page.getByTestId("models-toast")).toContainText("Applied — the agent uses it from its next step");
  await expect(page.getByTestId("selected-main")).toContainText("openai/e2e-model-alpha");
  await expect(page.getByTestId("selected-main")).toContainText("openai");
  await page.getByTestId("model-search").fill("embedding");
  await expect(page.getByTestId("use-main-text-embedding-3-small")).toHaveCount(0);
  await expect(page.getByTestId("use-embedding-text-embedding-3-small")).toBeVisible();
  await shot(page, "models-selected");

  // no restart: the very next chat turn is routed to the selected model
  const s = await createSession(request, "e2e models");
  await chatAndWait(request, s.id, "which model?");
  const usage = (await (await request.get(`/api/usage?session_id=${s.id}`)).json()) as { model: string }[];
  expect(usage.map((u) => u.model)).toContain("openai/e2e-model-alpha");

  // the Environment tab shows the DB as the source and links back here
  await page.goto("/settings#environment");
  await page.getByTestId("env-search").fill("AUOREO_MODEL");
  await expect(page.getByTestId("env-source-AUOREO_MODEL")).toHaveText("Saved here");
  await page.getByTestId("env-models-link-AUOREO_MODEL").click();
  await expect(page.getByTestId("models-panel")).toBeVisible();

  // remove: it serves the main model, so the UI asks first
  page.once("dialog", (d) => void d.accept());
  await page.getByTestId("provider-remove-openai").click();
  await expect(page.getByTestId("providers-empty")).toBeVisible();
  expect(await connections(request)).toEqual([]);
  await shot(page, "models-removed");
});
