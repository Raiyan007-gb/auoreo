import { expect, type Page, test } from "@playwright/test";
import { chatAndWait, createSession } from "./helpers";

/**
 * Keyboard gate: the first Tab stop is "Skip to content", Tab never gets stuck, and every stop
 * shows a visible focus indicator (an outline or a ring), including the custom chips, segmented
 * controls and tab lists that are not native buttons.
 */
const STOPS = 70;

interface Stop {
  label: string;
  visible: boolean;
}

/** Tab through the page; for each stop report its label and whether it shows a focus indicator. */
async function tabThrough(page: Page, count: number): Promise<Stop[]> {
  const stops: Stop[] = [];
  for (let i = 0; i < count; i++) {
    await page.keyboard.press("Tab");
    stops.push(
      await page.evaluate(() => {
        const el = document.activeElement as HTMLElement | null;
        if (!el || el === document.body) return { label: "(body)", visible: true };
        const cs = getComputedStyle(el);
        const outline = cs.outlineStyle !== "none" && Number.parseFloat(cs.outlineWidth) > 0 && !/rgba\(\d+,\s*\d+,\s*\d+,\s*0\)/.test(cs.outlineColor);
        const ring = cs.boxShadow !== "none" && /rgba?\(/.test(cs.boxShadow);
        const name = el.getAttribute("aria-label") || el.textContent?.trim().replace(/\s+/g, " ").slice(0, 40) || el.getAttribute("title") || "";
        const role = el.getAttribute("role") || el.tagName.toLowerCase();
        return { label: `${role} "${name}"`, visible: outline || ring };
      }),
    );
  }
  return stops;
}

let sampleId = "";
test.beforeAll(async ({ request }) => {
  const s = await createSession(request, "focus sample");
  sampleId = s.id;
  await chatAndWait(request, s.id, "hello keyboard");
});

for (const theme of ["light", "dark"] as const) {
  for (const path of ["/sessions", "/inbox", "/settings", "/doctor", "/usage", "SESSION"]) {
    test(`keyboard ${theme} ${path}`, async ({ page }) => {
      await page.addInitScript((t) => localStorage.setItem("auoreo_theme", t), theme);
      await page.goto(path === "SESSION" ? `/session/${sampleId}` : path);
      await expect(page.getByTestId("ws-state")).toHaveAttribute("data-state", /open|unavailable/);
      await page.locator("main").waitFor();
      await page.waitForTimeout(path === "/doctor" ? 6000 : 700);

      const stops = await tabThrough(page, STOPS);
      expect(stops[0]?.label, "first Tab stop").toContain("Skip to content");
      expect(new Set(stops.map((s) => s.label)).size, "Tab keeps moving").toBeGreaterThan(15);
      const bare = stops.filter((s) => !s.visible).map((s) => s.label);
      expect(bare, "tab stops without a visible focus indicator").toEqual([]);
    });
  }
}

test("segmented controls and tab lists are reachable with the keyboard", async ({ page }) => {
  await page.goto("/settings");
  await page.getByRole("tab", { name: "General" }).click();
  await page.getByRole("radio", { name: "Dark" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.getByRole("radio", { name: "Light" }).focus();
  await page.keyboard.press("Space");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await page.getByRole("radio", { name: "Compact" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.locator("html")).toHaveAttribute("data-density", "compact");
  await page.getByRole("radio", { name: "Comfortable" }).click(); // leave the default behind
});
