import { expect, test } from "@playwright/test";
import { shot } from "./helpers";

interface VoiceStatus {
  enabled: boolean;
  backends: { kind: string; name: string | null; ok: boolean }[];
}

/** 16 kHz mono int16 WAV: tone A, gap, quieter higher tone B (two "speakers" for the fake diarizer). */
function twoToneWav(comment: string): Buffer {
  const rate = 16_000;
  const parts: [number, number, number][] = [
    [440, 0.5, 1.0],
    [0, 0, 0.6],
    [1200, 0.25, 1.0],
    [0, 0, 0.4],
  ];
  const total = parts.reduce((n, [, , s]) => n + Math.round(s * rate), 0);
  const pcm = Buffer.alloc(total * 2);
  let off = 0;
  for (const [freq, amp, secs] of parts) {
    const n = Math.round(secs * rate);
    for (let i = 0; i < n; i++, off += 2)
      pcm.writeInt16LE(Math.round(amp * 32767 * Math.sin((2 * Math.PI * freq * i) / rate)), off);
  }
  let text = Buffer.from(`${comment}\0`, "utf8");
  if (text.length % 2) text = Buffer.concat([text, Buffer.from([0])]);
  const icmt = Buffer.concat([Buffer.from("ICMT"), u32(text.length), text]);
  const list = Buffer.concat([Buffer.from("LIST"), u32(4 + icmt.length), Buffer.from("INFO"), icmt]);
  const fmt = Buffer.alloc(16);
  fmt.writeUInt16LE(1, 0);
  fmt.writeUInt16LE(1, 2);
  fmt.writeUInt32LE(rate, 4);
  fmt.writeUInt32LE(rate * 2, 8);
  fmt.writeUInt16LE(2, 12);
  fmt.writeUInt16LE(16, 14);
  const chunks = Buffer.concat([
    Buffer.from("fmt "),
    u32(16),
    fmt,
    list,
    Buffer.from("data"),
    u32(pcm.length),
    pcm,
  ]);
  return Buffer.concat([Buffer.from("RIFF"), u32(4 + chunks.length), Buffer.from("WAVE"), chunks]);
}

function u32(n: number): Buffer {
  const b = Buffer.alloc(4);
  b.writeUInt32LE(n, 0);
  return b;
}

test("meetings: upload a recording, finalise, speaker-labelled transcript, summarise", async ({
  page,
  request,
}) => {
  const status = (await (await request.get("/api/voice/status")).json()) as VoiceStatus;
  const stt = status.backends.find((b) => b.kind === "stt");
  test.skip(!status.enabled || stt?.name !== "fake", "needs the fake stt backend (npm run e2e sets it)");

  await page.goto("/meetings");
  await page.getByLabel("Meeting title").fill("e2e weekly sync");
  await page.getByRole("button", { name: "Start" }).click();
  const panel = page.getByTestId("meeting-panel");
  await expect(panel).toHaveAttribute("data-status", "recording");
  await expect(panel).toContainText("e2e weekly sync");

  await page.getByTestId("meeting-upload").setInputFiles({
    name: "sync.wav",
    mimeType: "audio/wav",
    buffer: twoToneWav("We agreed to ship on Friday."),
  });
  await expect(panel).toContainText("1 chunk");
  await panel.getByRole("button", { name: "Finalise" }).click();
  await expect(panel).toHaveAttribute("data-status", "finalised");

  const segs = page.getByTestId("meeting-segment");
  await expect(segs).toHaveCount(2);
  await expect(segs.nth(0)).toHaveAttribute("data-speaker", "SPEAKER_00");
  await expect(segs.nth(1)).toHaveAttribute("data-speaker", "SPEAKER_01");
  await expect(segs.nth(0)).toContainText("We agreed to ship on Friday.");
  const row = page.locator('[data-testid="meeting-row"]', { hasText: "e2e weekly sync" });
  await expect(row).toContainText("finalised");

  await panel.getByRole("button", { name: "Summarise" }).click();
  await expect(page.getByTestId("meeting-summary")).toBeVisible({ timeout: 15_000 });
  await expect(panel.getByRole("link", { name: "summary session" })).toBeVisible();
  await shot(page, "meetings");
});
