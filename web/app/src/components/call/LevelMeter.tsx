import { useEffect, useRef } from "react";

/** Plain `<canvas>` level meter (no dependency): recent `rtc.audio_level` RMS values as bars. */
export function LevelMeter({ levels, threshold = 0.02, width = 160, height = 28 }: { levels: readonly number[]; threshold?: number; width?: number; height?: number }) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const c = ref.current;
    const g = c?.getContext("2d");
    if (!c || !g) return;
    const style = getComputedStyle(c);
    const fg = style.getPropertyValue("--success").trim() || "#16a34a";
    const mute = style.getPropertyValue("--muted-foreground").trim() || "#888";
    g.clearRect(0, 0, width, height);
    const n = Math.max(1, Math.floor(width / 4));
    const recent = levels.slice(-n);
    // RMS of speech is ~0.01–0.3: map on a sqrt scale so quiet speech still shows.
    recent.forEach((v, i) => {
      const h = Math.max(1, Math.min(1, Math.sqrt(Math.max(0, v) / 0.3)) * height);
      g.fillStyle = v > threshold ? fg : mute;
      g.fillRect(i * 4, height - h, 3, h);
    });
    const ty = height - Math.min(1, Math.sqrt(threshold / 0.3)) * height;
    g.fillStyle = mute;
    g.fillRect(0, ty, width, 1);
  }, [levels, threshold, width, height]);
  return (
    <canvas
      ref={ref}
      width={width}
      height={height}
      role="img"
      aria-label={`Input level ${levels.length ? levels[levels.length - 1]?.toFixed(3) : "0"}`}
      data-testid="level-meter"
      data-samples={levels.length}
      className="rounded border bg-muted/30"
    />
  );
}
