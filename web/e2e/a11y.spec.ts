import AxeBuilder from "@axe-core/playwright";
import { mkdirSync, writeFileSync } from "node:fs";
import { expect, type Page, test } from "@playwright/test";
import { chatAndWait, createSession, openMore } from "./helpers";

/**
 * Accessibility gate (WCAG 2.1 A/AA via axe-core) on every page, light and dark. Colour contrast is
 * part of AA, so this is also the check for muted text, pills and badges in both themes.
 * `A11Y_REPORT=1` writes `test-results/a11y-report.json` and never fails (triage mode).
 */
const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"];
const PAGES = [
  "/sessions",
  "/inbox",
  "/memory",
  "/goals",
  "/meetings",
  "/harness",
  "/hosts",
  "/skills",
  "/plugins",
  "/teams",
  "/jobs",
  "/webhooks",
  "/platforms",
  "/usage",
  "/audit",
  "/doctor",
  "/settings",
  "/welcome",
];
const REPORT = Boolean(process.env.A11Y_REPORT);
const found: Record<string, { rule: string; impact: string; help: string; nodes: number; where: string[] }> = {};

test.describe.configure({ mode: "serial" });

let sampleId = "";
let sharePath = "";
test.beforeAll(async ({ request }) => {
  // Something to look at: a finished conversation, a share link, a goal, a fact.
  const s = await createSession(request, "a11y sample");
  sampleId = s.id;
  await chatAndWait(request, s.id, "hello accessibility");
  const share = (await (await request.post(`/api/sessions/${s.id}/share`)).json()) as { token: string };
  sharePath = `/share/${share.token}`; // the page route (the response's `path` is the API one)
  await request.post("/api/goals", { data: { title: "Keep the contrast above AA" } });
  await request.post("/api/memory/facts", { data: { content: "a11y sample fact", category: "user" } });
});

/** Pages reached by URL, plus the states you only reach by interacting (tabs, dialogs, menus). */
interface Scene {
  name: string;
  open: (page: Page) => Promise<void>;
}
const go = (path: string, wait = 900): Scene => ({
  name: path,
  open: async (page) => {
    await page.goto(path);
    await expect(page.getByTestId("ws-state")).toHaveAttribute("data-state", /open|unavailable/);
    await page.locator("main").waitFor();
    await page.waitForTimeout(wait); // let skeletons settle
  },
});
const SCENES: Scene[] = [
  ...PAGES.map((p) => go(p, p === "/doctor" ? 6000 : 900)),
  {
    name: "/session/:id",
    open: async (page) => {
      await go(`/session/${sampleId}`).open(page);
    },
  },
  {
    name: "session more menu",
    open: async (page) => {
      await go(`/session/${sampleId}`).open(page);
      await openMore(page);
    },
  },
  {
    name: "session details sheet",
    open: async (page) => {
      await page.setViewportSize({ width: 1000, height: 760 });
      await go(`/session/${sampleId}`).open(page);
      await page.getByRole("button", { name: "Session details" }).click();
      await page.getByRole("dialog").waitFor();
    },
  },
  {
    name: "share page",
    open: async (page) => {
      await page.goto(sharePath);
      await page.getByText("Shared from AUOREO").waitFor();
    },
  },
  {
    name: "new session dialog",
    open: async (page) => {
      await go("/sessions").open(page);
      await page.getByRole("button", { name: "New session" }).click();
      await page.getByRole("dialog").waitFor();
    },
  },
  {
    name: "command palette",
    open: async (page) => {
      await go("/sessions").open(page);
      await page.keyboard.press("Control+k");
      await page.getByRole("dialog").waitFor();
    },
  },
  ...["Models", "Environment", "General"].map(
    (tab): Scene => ({
      name: `settings ${tab}`,
      open: async (page) => {
        await go("/settings").open(page);
        await page.getByRole("tab", { name: tab }).click();
        await page.waitForTimeout(600);
      },
    }),
  ),
  ...[
    ["/plugins", "MCP servers"],
    ["/jobs", "Webhooks"],
    ["/jobs", "Deliveries"],
    ["/teams", "Kanban"],
    ["/teams", "Blackboard"],
    ["/memory", "Facts"],
    ["/memory", "Files"],
    ["/memory", "Curator"],
    ["/goals", "Suggestions"],
    ["/skills", "Hub"],
  ].map(
    ([path, tab]): Scene => ({
      name: `${path} ${tab}`,
      open: async (page) => {
        await go(path as string).open(page);
        await page.getByRole("tab", { name: tab }).click();
        await page.waitForTimeout(600);
      },
    }),
  ),
  {
    name: "sidebar folded",
    open: async (page) => {
      await go("/sessions").open(page);
      await page.getByRole("button", { name: "Work section" }).click();
    },
  },
];

for (const theme of ["light", "dark"] as const) {
  for (const scene of SCENES) {
    test(`a11y ${theme} ${scene.name}`, async ({ page }) => {
      await page.addInitScript((t) => localStorage.setItem("auoreo_theme", t), theme);
      await scene.open(page);
      const { violations } = await new AxeBuilder({ page }).withTags(TAGS).analyze();
      for (const v of violations) {
        const k = `${theme}|${v.id}`;
        const row = (found[k] ??= { rule: v.id, impact: v.impact ?? "", help: v.help, nodes: 0, where: [] });
        row.nodes += v.nodes.length;
        for (const n of v.nodes.slice(0, 3)) {
          const why = (n.failureSummary ?? "").replace(/\s+/g, " ").slice(0, 170);
          row.where.push(`${scene.name} ${n.target.join(" ")} :: ${why}`);
        }
      }
      if (!REPORT) {
        const brief = violations.map((v) => `${v.id} (${v.nodes.length}): ${v.nodes[0]?.target.join(" ")}`);
        expect(brief, `${theme} ${scene.name}`).toEqual([]);
      }
    });
  }
}

test.afterAll(() => {
  if (!REPORT) return;
  mkdirSync("test-results", { recursive: true });
  writeFileSync("test-results/a11y-report.json", JSON.stringify(Object.values(found), null, 1));
});
