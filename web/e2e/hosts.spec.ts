import { expect, test } from "@playwright/test";
import { shot } from "./helpers";

/** M7 Hosts page (`/api/hosts`, `m7.ssh.web` on in the e2e server): add, edit, check and delete
 * a host. Key material never appears: the key is a secret NAME and trust is a CLI step. */

const PASTED = "-----BEGIN OPENSSH PRIVATE KEY-----";

test("add, edit, check and delete a host; nothing trusts a key or shows key text", async ({ page, request }) => {
  await page.goto("/hosts");
  await expect(page.getByRole("heading", { name: "Hosts" })).toBeVisible();
  await page.getByTestId("host-add").click();
  const dialog = page.getByTestId("host-dialog");
  await dialog.getByTestId("host-name").fill("e2e_lab");
  await dialog.getByTestId("host-key").fill("SSH_KEY_E2E_LAB");
  await dialog.getByTestId("host-host").fill("127.0.0.1");
  await dialog.getByTestId("host-port").fill("9");
  await dialog.getByTestId("host-user").fill("ops");
  await dialog.getByTestId("host-tags").fill("readonly, lab");
  await dialog.getByTestId("host-save").click();
  const row = page.locator('[data-testid="host-row"][data-name="e2e_lab"]');
  await expect(row).toBeVisible();
  await expect(row.getByTestId("host-trust")).toContainText("auoreo ssh test e2e_lab");
  await expect(row.getByTestId("host-key-state")).toContainText("missing");
  await expect(row).toContainText("readonly");
  await shot(page, "hosts");

  // a pasted private key is refused (422) and never echoed back
  await row.getByRole("button", { name: "Edit e2e_lab" }).click();
  await dialog.getByTestId("host-key").fill(PASTED);
  await dialog.getByTestId("host-save").click();
  await expect(dialog.getByRole("alert")).toBeVisible();
  await expect(dialog.getByRole("alert")).not.toContainText("BEGIN");
  await dialog.getByTestId("host-key").fill("SSH_KEY_E2E_OTHER");
  await dialog.getByTestId("host-save").click();
  await expect(row).toContainText("SSH_KEY_E2E_OTHER");

  // check never trusts an unknown host key
  await row.getByRole("button", { name: "Check e2e_lab" }).click();
  await expect(row.getByTestId("host-check")).toContainText("auoreo ssh test e2e_lab");
  const body = (await (await request.get("/api/hosts")).json()) as { hosts: { name: string; trusted: boolean }[] };
  expect(body.hosts.find((h) => h.name === "e2e_lab")?.trusted).toBe(false);
  expect(await page.content()).not.toContain("PRIVATE KEY");

  await row.getByRole("button", { name: "Delete e2e_lab" }).click();
  await page.getByTestId("host-delete-confirm").click();
  await expect(row).toHaveCount(0);
});

test("the hosts API refuses unknown fields without echoing them", async ({ request }) => {
  const r = await request.put("/api/hosts/e2e_bad", {
    data: { host: "127.0.0.1", user: "ops", key_name: "K", private_key: PASTED },
  });
  expect(r.status()).toBe(422);
  expect(await r.text()).not.toContain("BEGIN");
});
