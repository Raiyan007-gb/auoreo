/**
 * Push-to-talk + read-aloud for the session composer (spec §26.9).
 * Hold the mic (pointer or Space/Enter) → MediaRecorder → POST /api/voice/transcribe with the
 * session id; the server submits the transcript as a followup, so it appears as the user message.
 * The speaker toggle plays /api/voice/speak for each finished assistant message.
 */
import type { VoiceStatus } from "@sdk/client";
import { LoaderIcon, MicIcon, Volume2Icon, VolumeXIcon } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Tip } from "@/components/ui/tooltip";
import { useAsync } from "@/hooks/useAsync";
import { api } from "@/lib/api";
import type { Item } from "@/lib/thread";
import { cn, errorText } from "@/lib/utils";

type Phase = "idle" | "recording" | "sending";

function readiness(status: VoiceStatus | undefined, kind: "stt" | "tts"): { ok: boolean; why: string } {
  if (!status) return { ok: false, why: "Checking voice backends…" };
  if (!status.enabled) return { ok: false, why: "Voice is disabled on the server (settings.voice_enabled)" };
  const row = status.backends.find((b) => b.kind === kind);
  if (!row) return { ok: false, why: `No ${kind} backend selected` };
  if (!row.ok) return { ok: false, why: `${kind} backend ${row.name ?? ""} not ready: ${row.remedy || row.detail}`.trim() };
  return { ok: true, why: "" };
}

const hasRecorder = () => typeof window !== "undefined" && typeof window.MediaRecorder !== "undefined" && !!navigator.mediaDevices?.getUserMedia;

function pickMime(): string | undefined {
  for (const m of ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"])
    if (MediaRecorder.isTypeSupported?.(m)) return m;
  return undefined;
}

export function VoiceButton({ sessionId, items }: { sessionId: string; items: readonly Item[] }) {
  const status = useAsync(() => api.voiceStatus(), []);
  const [phase, setPhase] = useState<Phase>("idle");
  const [speakOn, setSpeakOn] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const rec = useRef<MediaRecorder | null>(null);
  const audio = useRef<HTMLAudioElement | null>(null);
  const spoken = useRef<string | null>(null);

  const stt = hasRecorder() ? readiness(status.data, "stt") : { ok: false, why: "This browser cannot record audio (no MediaRecorder)" };
  const tts = readiness(status.data, "tts");

  let last: Extract<Item, { kind: "assistant" }> | undefined;
  for (let i = items.length - 1; i >= 0 && !last; i--) {
    const it = items[i];
    if (it?.kind === "assistant") last = it;
  }
  const lastKey = last && !last.streaming && last.text.trim() ? last.key : null;
  const lastText = last?.text ?? "";

  const play = async (text: string) => {
    try {
      const blob = await api.speak(text, sessionId);
      audio.current?.pause();
      const el = new Audio(URL.createObjectURL(blob));
      el.onended = () => URL.revokeObjectURL(el.src);
      audio.current = el;
      await el.play();
    } catch (e) {
      setErr(errorText(e));
    }
  };

  // Read each newly finished assistant message aloud while the speaker is on.
  useEffect(() => {
    if (!speakOn || !lastKey || spoken.current === lastKey) return;
    spoken.current = lastKey;
    void play(lastText);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- keyed on the message, not the closure
  }, [speakOn, lastKey]);

  useEffect(() => () => audio.current?.pause(), []);

  const start = async () => {
    if (phase !== "idle" || !stt.ok) return;
    setErr(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mime = pickMime();
      const r = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
      const chunks: Blob[] = [];
      r.ondataavailable = (e) => e.data.size && chunks.push(e.data);
      r.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        const type = r.mimeType || mime || "audio/webm";
        const blob = new Blob(chunks, { type });
        if (!blob.size) return setPhase("idle");
        setPhase("sending");
        try {
          const ext = type.includes("ogg") ? "ogg" : type.includes("mp4") ? "m4a" : "webm";
          await api.transcribe(blob, sessionId, `speech.${ext}`);
        } catch (e) {
          setErr(errorText(e));
        }
        setPhase("idle");
      };
      rec.current = r;
      r.start();
      setPhase("recording");
    } catch (e) {
      setErr(errorText(e));
      setPhase("idle");
    }
  };

  const stop = () => {
    if (rec.current?.state === "recording") rec.current.stop();
    rec.current = null;
  };

  const micLabel = !stt.ok ? stt.why : phase === "recording" ? "Release to send" : phase === "sending" ? "Transcribing…" : "Hold to talk";
  const speakLabel = !tts.ok ? tts.why : speakOn ? "Stop reading replies aloud" : "Read replies aloud";

  return (
    <div className="flex gap-1" data-testid="voice-controls">
      <Tip label={err ?? micLabel} wrap={!stt.ok}>
        <Button
          type="button"
          variant={phase === "recording" ? "destructive" : "outline"}
          size="icon-sm"
          aria-label="Push to talk"
          aria-pressed={phase === "recording"}
          data-testid="voice-button"
          data-ready={stt.ok}
          disabled={!stt.ok || phase === "sending"}
          className={cn(phase === "recording" && "animate-pulse")}
          onPointerDown={(e) => {
            e.preventDefault();
            void start();
          }}
          onPointerUp={stop}
          onPointerLeave={stop}
          onKeyDown={(e) => {
            if ((e.key === " " || e.key === "Enter") && !e.repeat) {
              e.preventDefault();
              void start();
            }
          }}
          onKeyUp={(e) => (e.key === " " || e.key === "Enter") && stop()}
        >
          {phase === "sending" ? <LoaderIcon className="animate-spin" /> : <MicIcon />}
        </Button>
      </Tip>
      <Tip label={speakLabel} wrap={!tts.ok}>
        <Button
          type="button"
          variant="outline"
          size="icon-sm"
          aria-label="Read replies aloud"
          aria-pressed={speakOn}
          data-testid="voice-speaker"
          data-ready={tts.ok}
          disabled={!tts.ok}
          onClick={() => {
            const on = !speakOn;
            setSpeakOn(on);
            if (!on) audio.current?.pause();
            else spoken.current = null; // turning it on reads the latest reply
          }}
        >
          {speakOn ? <Volume2Icon /> : <VolumeXIcon />}
        </Button>
      </Tip>
    </div>
  );
}
