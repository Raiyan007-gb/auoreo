import type { RtcCapabilities, ServerEvent } from "@sdk/client";
import { MicIcon, MicOffIcon, PhoneIcon, PhoneOffIcon, ScreenShareIcon, VideoIcon, Volume2Icon } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { ErrorNote } from "@/components/AppShell";
import { LevelMeter } from "@/components/call/LevelMeter";
import { SharedScreen } from "@/components/call/SharedScreen";
import { type TranscriptLine, TranscriptPane } from "@/components/call/TranscriptPane";
import { Badge, type BadgeVariant } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { useEvents } from "@/lib/eventsHub";
import { LiveKitCall } from "@/lib/livekitCall";
import { type CallSnapshot, IDLE_CALL, RtcCall } from "@/lib/rtc";
import { cn } from "@/lib/utils";

const VOICE_TYPES = ["voice.transcript", "voice.speaking", "voice.spoke", "voice.barge_in", "voice.speech_cancelled", "voice.error"];
const LEVEL_TYPES = ["rtc.audio_level"];
const STATE_VARIANT: Record<string, BadgeVariant> = {
  connected: "success",
  completed: "success",
  checking: "info",
  connecting: "info",
  new: "outline",
  disconnected: "warning",
  failed: "destructive",
  closed: "outline",
};
const MAX_LEVELS = 60;

function StateBadge({ label, state }: { label: string; state: string }) {
  return (
    <Badge variant={STATE_VARIANT[state] ?? "outline"} data-testid={`call-${label.toLowerCase()}`} data-state={state}>
      {label} {state}
    </Badge>
  );
}

/**
 * Voice/video call for one session over WebRTC (spec §17 voice widget): mic/cam/share toggles,
 * `AudioControls{muted, suppressed}`, ICE/DTLS badges, live transcript, VAD dot + level meter,
 * barge badge. Approval cards stay on the WS control plane (the session page), never here.
 */
export function CallPanel({ sessionId }: { sessionId: string }) {
  const [snap, setSnap] = useState<CallSnapshot>(IDLE_CALL);
  const [caps, setCaps] = useState<RtcCapabilities | null>(null);
  const [allowControl, setAllowControl] = useState(false);
  const [useLiveKit, setUseLiveKit] = useState(false);
  const [levels, setLevels] = useState<number[]>([]);
  const [speaking, setSpeaking] = useState(false);
  const [agentSpeaking, setAgentSpeaking] = useState(false);
  const [barge, setBarge] = useState(false);
  const [lines, setLines] = useState<TranscriptLine[]>([]);
  const [frames, setFrames] = useState<string[]>([]);
  const [local, setLocal] = useState<MediaStream | null>(null);
  const call = useRef<RtcCall | LiveKitCall | null>(null);
  const audio = useRef<HTMLAudioElement>(null);
  const bargeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const inCall = snap.phase === "connecting" || snap.phase === "connected";

  useEffect(() => {
    api.rtcCapabilities().then(setCaps).catch(() => setCaps(null));
    return () => {
      void call.current?.stop();
      if (bargeTimer.current) clearTimeout(bargeTimer.current);
    };
  }, []);

  const onVoice = (e: ServerEvent) => {
    const d = e.data;
    if (snap.rtcId && d.rtc_id && d.rtc_id !== snap.rtcId) return;
    if (e.type === "voice.transcript") {
      setLines((l) => [...l, { seq: e.seq, text: String(d.text ?? ""), at: e.created_at, kind: "user" as const }].slice(-200));
    } else if (e.type === "voice.speaking") setAgentSpeaking(true);
    else if (e.type === "voice.spoke" || e.type === "voice.speech_cancelled") setAgentSpeaking(false);
    else if (e.type === "voice.barge_in") {
      setBarge(true);
      if (bargeTimer.current) clearTimeout(bargeTimer.current);
      bargeTimer.current = setTimeout(() => setBarge(false), 4000);
    } else if (e.type === "voice.error") {
      setLines((l) => [...l, { seq: e.seq, text: `STT error: ${String(d.message ?? "")}`, at: e.created_at, kind: "error" as const }]);
    }
  };
  useEvents({ channel: sessionId, afterSeq: "live", types: VOICE_TYPES, enabled: inCall }, onVoice);
  useEvents({ channel: "server", afterSeq: "live", types: LEVEL_TYPES, enabled: inCall }, (e) => {
    if (e.type !== "rtc.audio_level" || e.data.rtc_id !== snap.rtcId) return;
    setLevels((l) => [...l, Number(e.data.rms ?? 0)].slice(-MAX_LEVELS));
    setSpeaking(Boolean(e.data.speaking));
  });

  const start = async () => {
    setLines([]);
    setLevels([]);
    setFrames([]);
    const onRemoteStream = (s: MediaStream) => {
      if (audio.current) {
        audio.current.srcObject = s;
        void audio.current.play().catch(() => undefined);
      }
    };
    if (caps?.livekit && useLiveKit) {
      const lk = new LiveKitCall(api, { sessionId, agentName: caps.livekit_agent ?? undefined, onChange: setSnap, onRemoteStream, onLocalVideo: setLocal });
      call.current = lk;
      await lk.start();
      return;
    }
    const c = new RtcCall(api, {
      sessionId,
      input: Boolean(caps?.teleop_input_enabled && allowControl),
      onChange: setSnap,
      onRemoteStream: (s) => {
        if (audio.current) {
          audio.current.srcObject = s;
          void audio.current.play().catch(() => undefined);
        }
      },
      onLocalVideo: setLocal,
      onChannelFrame: (f) => setFrames((x) => [...x.slice(-49), String(f.type ?? "?")]),
    });
    call.current = c;
    await c.start();
  };
  const stop = async () => {
    await call.current?.stop();
    call.current = null;
    setSpeaking(false);
    setAgentSpeaking(false);
    if (audio.current) audio.current.srcObject = null;
  };

  const teleop = caps?.teleop_input_enabled ?? false;
  return (
    <section aria-label="Call" data-testid="call-panel" data-phase={snap.phase} className="space-y-2 border-b bg-card px-4 py-3">
      <div className="flex flex-wrap items-center gap-2">
        {inCall ? (
          <Button size="sm" variant="destructive" onClick={() => void stop()} data-testid="call-stop">
            <PhoneOffIcon aria-hidden /> Hang up
          </Button>
        ) : (
          <Button variant="brand" size="sm" onClick={() => void start()} disabled={caps?.available === false} data-testid="call-start">
            <PhoneIcon aria-hidden /> Start call
          </Button>
        )}
        <Button
          size="icon-sm"
          variant={snap.audio.muted ? "destructive" : "outline"}
          aria-pressed={snap.audio.muted}
          aria-label={snap.audio.muted ? "Unmute microphone" : "Mute microphone"}
          disabled={!inCall}
          onClick={() => call.current?.setAudio({ muted: !snap.audio.muted })}
          data-testid="call-mute"
        >
          {snap.audio.muted ? <MicOffIcon /> : <MicIcon />}
        </Button>
        <Button
          size="sm"
          variant={snap.audio.suppressed ? "secondary" : "outline"}
          aria-pressed={snap.audio.suppressed}
          disabled={!inCall}
          onClick={() => call.current?.setAudio({ suppressed: !snap.audio.suppressed })}
          title="Echo cancellation + noise suppression"
        >
          Suppress {snap.audio.suppressed ? "on" : "off"}
        </Button>
        <Button
          size="icon-sm"
          variant={snap.video === "camera" ? "secondary" : "outline"}
          aria-pressed={snap.video === "camera"}
          aria-label={snap.video === "camera" ? "Stop camera" : "Start camera"}
          disabled={snap.phase !== "connected"}
          onClick={() => void call.current?.setVideo(snap.video === "camera" ? "none" : "camera")}
          data-testid="call-camera"
        >
          <VideoIcon />
        </Button>
        <Button
          size="icon-sm"
          variant={snap.video === "screen" ? "secondary" : "outline"}
          aria-pressed={snap.video === "screen"}
          aria-label={snap.video === "screen" ? "Stop sharing screen" : "Share screen"}
          disabled={snap.phase !== "connected"}
          onClick={() => void call.current?.setVideo(snap.video === "screen" ? "none" : "screen")}
          data-testid="call-share"
        >
          <ScreenShareIcon />
        </Button>
        <label className={cn("flex items-center gap-1.5 text-xs", !teleop && "text-muted-foreground")} title={teleop ? "Lets clicks/typing on the shared screen drive the computer tool (each still goes through permissions)" : "Disabled on the server (settings.teleop_input_enabled)"}>
          <input
            type="checkbox"
            role="switch"
            checked={allowControl && teleop}
            disabled={!teleop || inCall}
            onChange={(e) => setAllowControl(e.target.checked)}
            data-testid="call-allow-control"
          />
          Allow remote control
        </label>
        {caps?.livekit && (
          <label className="flex items-center gap-1.5 text-xs" title="Join through the LiveKit SFU instead of the direct (P2P) connection">
            <input
              type="checkbox"
              role="switch"
              checked={useLiveKit}
              disabled={inCall}
              onChange={(e) => setUseLiveKit(e.target.checked)}
              data-testid="call-livekit"
            />
            LiveKit
          </label>
        )}
        <div className="ml-auto flex flex-wrap items-center gap-1.5" aria-label="Call state">
          <span
            aria-label={speaking ? "Voice detected" : "No voice"}
            title="VAD (rtc.audio_level)"
            data-testid="vad-dot"
            data-speaking={speaking}
            className={cn("size-2.5 rounded-full", speaking ? "bg-success" : "bg-muted-foreground/40")}
          />
          <LevelMeter levels={levels} />
          {agentSpeaking && (
            <Badge variant="info">
              <Volume2Icon aria-hidden /> speaking
            </Badge>
          )}
          {barge && (
            <Badge variant="warning" data-testid="barge-badge">
              barge-in
            </Badge>
          )}
          <StateBadge label="ICE" state={snap.ice} />
          <StateBadge label="DTLS" state={snap.dtls} />
          {snap.remoteAudio && (
            <Badge variant="success" data-testid="remote-audio">
              audio in
            </Badge>
          )}
          {snap.input && <Badge variant="warning">remote control</Badge>}
        </div>
      </div>
      <ErrorNote error={snap.error} />
      <audio ref={audio} autoPlay data-testid="call-audio" className="hidden" />
      {(inCall || lines.length > 0) && <TranscriptPane lines={lines} />}
      <SharedScreen stream={local} control={snap.input && snap.video === "screen"} onInput={(e) => call.current?.sendInput(e) ?? false} />
      {frames.length > 0 && (
        <details className="text-xs">
          <summary className="cursor-pointer text-muted-foreground">auoreo-events ({frames.length}) · SDP [REDACTED]</summary>
          <p className="mt-1 font-mono break-all text-muted-foreground" data-testid="call-frames">
            {frames.join(" · ")}
          </p>
        </details>
      )}
    </section>
  );
}
