// G5b: browser WebRTC call client against the aiortc server (fake mic → energy VAD → fake STT).
// Runs in the `chromium-media` project (fake capture devices; see playwright.config.ts).
import { expect, test } from "@playwright/test";
import { createSession, shot } from "./helpers";

test("call connects, plays the server's audio track, transcribes, and hangs up cleanly", async ({ page, request }) => {
  test.setTimeout(90_000);
  const caps = (await (await request.get("/api/rtc/capabilities")).json()) as { available: boolean; teleop_input_enabled: boolean };
  test.skip(!caps.available, "server lacks the rtc extra (aiortc)");
  const s = await createSession(request, "e2e call");
  await page.goto(`/session/${s.id}`);

  const iceFetch = page.waitForResponse((r) => r.url().endsWith("/api/rtc/ice") && r.ok());
  await page.getByTestId("call-toggle").click();
  const panel = page.getByTestId("call-panel");
  await expect(panel).toBeVisible();
  // Teleop is off on the server, so the remote-control switch is disabled.
  await expect(page.getByTestId("call-allow-control")).toBeDisabled();
  const offer = page.waitForRequest((r) => r.url().endsWith("/api/rtc/offer") && r.method() === "POST");
  await page.getByTestId("call-start").click();
  await iceFetch; // fetched right before the offer
  const body = (await offer).postDataJSON() as { session_id: string; input: boolean; sdp: string };
  expect(body.session_id).toBe(s.id);
  expect(body.input).toBe(false);
  expect(body.sdp).toContain("a=candidate"); // non-trickle: gathered before sending

  await expect(panel).toHaveAttribute("data-phase", "connected", { timeout: 20_000 });
  await expect(page.getByTestId("call-ice")).toHaveAttribute("data-state", /connected|completed/);
  await expect(page.getByTestId("call-dtls")).toHaveAttribute("data-state", "connected", { timeout: 10_000 });
  await expect(page.getByTestId("remote-audio")).toBeVisible();
  const remoteTracks = await page.getByTestId("call-audio").evaluate((el) => {
    const src = (el as HTMLAudioElement).srcObject as MediaStream | null;
    return src ? src.getAudioTracks().map((t) => t.readyState) : [];
  });
  expect(remoteTracks).toEqual(["live"]);

  // VAD level samples arrive on the shared event stream (rtc.audio_level).
  await expect.poll(async () => Number(await page.getByTestId("level-meter").getAttribute("data-samples")), { timeout: 15_000 }).toBeGreaterThan(0);
  const line = page.getByTestId("transcript-line").filter({ hasText: "fake transcript (" }).first();
  await expect(line).toBeVisible({ timeout: 30_000 });

  // Camera toggle attaches a video track via replaceTrack (no renegotiation).
  await page.getByTestId("call-camera").click();
  await expect(page.getByTestId("shared-view")).toBeVisible();
  await page.getByTestId("call-mute").click();
  await expect(page.getByTestId("call-mute")).toHaveAttribute("aria-pressed", "true");
  await shot(page, "call-panel");

  await page.getByTestId("call-stop").click();
  await expect(panel).toHaveAttribute("data-phase", "closed");
  await expect(page.getByTestId("shared-view")).toHaveCount(0);
  await expect.poll(async () => ((await (await request.get("/api/rtc")).json()) as unknown[]).length, { timeout: 10_000 }).toBe(0);
  // The transcript stays readable after hanging up.
  await expect(line).toBeVisible();
});
