/**
 * Browser WebRTC call client for `POST /api/rtc/offer` (aiortc on the server).
 *
 * - ICE servers come from `GET /api/rtc/ice` right before every offer (TURN creds expire).
 * - Non-trickle: the offer is sent once ICE gathering completed.
 * - Mic via `getUserMedia({audio})` (sendrecv: the server's speech comes back on it).
 * - A send-only video transceiver is negotiated up front, so the camera and the screen share
 *   are attached later with `RTCRtpSender.replaceTrack()` (same kind → no renegotiation).
 * - `auoreo-events` (read-only mirror of durable events) is created before the offer;
 *   `auoreo-input` (teleop) only when the caller asked for input and the server allows it.
 *   Approvals never travel on a data channel: they stay on the `/ws` control plane.
 */
import type { RtcAnswer, RtcIceConfig } from "@sdk/client";

export interface RtcApi {
  rtcIce: () => Promise<RtcIceConfig>;
  rtcOffer: (b: { sdp: string; session_id?: string | null; speak?: boolean | null; input?: boolean }) => Promise<RtcAnswer>;
  rtcClose: (id: string) => Promise<unknown>;
}

/** `AudioControls{muted, suppressed}` (spec §17 voice widget). */
export interface AudioControls {
  /** Mic track disabled (silence is sent). */
  readonly muted: boolean;
  /** Echo cancellation + noise suppression constraints on the mic track. */
  readonly suppressed: boolean;
}

export type VideoSource = "none" | "camera" | "screen";

export interface CallSnapshot {
  readonly phase: "idle" | "connecting" | "connected" | "closed" | "failed";
  readonly rtcId: string | null;
  readonly ice: RTCIceConnectionState | "new";
  readonly dtls: RTCDtlsTransportState | "new";
  readonly remoteAudio: boolean;
  readonly audio: AudioControls;
  readonly video: VideoSource;
  readonly input: boolean;
  readonly error: string | null;
}

export const IDLE_CALL: CallSnapshot = {
  phase: "idle",
  rtcId: null,
  ice: "new",
  dtls: "new",
  remoteAudio: false,
  audio: { muted: false, suppressed: true },
  video: "none",
  input: false,
  error: null,
};

export interface CallOptions {
  readonly sessionId: string;
  /** Ask for the `auoreo-input` teleop channel (`input: true`; 403 unless teleop is enabled). */
  readonly input?: boolean;
  readonly onChange: (s: CallSnapshot) => void;
  readonly onRemoteStream: (s: MediaStream) => void;
  /** Local preview of the camera / shared screen (null when stopped). */
  readonly onLocalVideo?: (s: MediaStream | null) => void;
  /** Raw `auoreo-events` frames (debug drawer). */
  readonly onChannelFrame?: (frame: Record<string, unknown>) => void;
}

/** Teleop input event (`auoreo-input`), coordinates relative 0–1000. */
export type InputEvent =
  | { op: "click"; x: number; y: number }
  | { op: "move"; x: number; y: number }
  | { op: "type"; text: string }
  | { op: "key"; key: string }
  | { op: "scroll"; direction: "up" | "down" | "left" | "right"; amount?: number; x?: number; y?: number };

const GATHER_TIMEOUT_MS = 4000;

function waitGathering(pc: RTCPeerConnection): Promise<void> {
  if (pc.iceGatheringState === "complete") return Promise.resolve();
  return new Promise((done) => {
    const t = setTimeout(done, GATHER_TIMEOUT_MS); // send what we have (host candidates)
    pc.addEventListener("icegatheringstatechange", () => {
      if (pc.iceGatheringState === "complete") {
        clearTimeout(t);
        done();
      }
    });
  });
}

/** Map a pixel position inside an element to the 0–1000 teleop grid. */
export function relCoords(clientX: number, clientY: number, rect: { left: number; top: number; width: number; height: number }) {
  const clamp = (v: number) => Math.max(0, Math.min(1000, Math.round(v)));
  return {
    x: clamp(((clientX - rect.left) / Math.max(1, rect.width)) * 1000),
    y: clamp(((clientY - rect.top) / Math.max(1, rect.height)) * 1000),
  };
}

export class RtcCall {
  private pc: RTCPeerConnection | null = null;
  private mic: MediaStreamTrack | null = null;
  private videoSender: RTCRtpSender | null = null;
  private videoTrack: MediaStreamTrack | null = null;
  private inputChannel: RTCDataChannel | null = null;
  private eventsChannel: RTCDataChannel | null = null; // keep a reference (never GC'd mid-call)
  private seq = 0;
  private s: CallSnapshot = IDLE_CALL;

  constructor(
    private readonly api: RtcApi,
    private readonly opts: CallOptions,
  ) {}

  get snapshot(): CallSnapshot {
    return this.s;
  }

  private set(patch: Partial<CallSnapshot>): void {
    this.s = { ...this.s, ...patch };
    this.opts.onChange(this.s);
  }

  async start(): Promise<void> {
    this.set({ ...IDLE_CALL, phase: "connecting", input: Boolean(this.opts.input) });
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      this.mic = stream.getAudioTracks()[0] ?? null;
      const ice = await this.api.rtcIce(); // fresh credentials for every offer
      const pc = new RTCPeerConnection({ iceServers: ice.iceServers.map((s) => ({ ...s, username: s.username ?? undefined, credential: s.credential ?? undefined })) });
      this.pc = pc;
      this.wire(pc);
      if (this.mic) pc.addTransceiver(this.mic, { direction: "sendrecv", streams: [stream] });
      else pc.addTransceiver("audio", { direction: "recvonly" });
      this.videoSender = pc.addTransceiver("video", { direction: "sendonly" }).sender;
      const events = pc.createDataChannel("auoreo-events");
      this.eventsChannel = events;
      events.onmessage = (ev: MessageEvent<string>) => {
        try {
          this.opts.onChannelFrame?.(JSON.parse(ev.data) as Record<string, unknown>);
        } catch {
          /* not JSON */
        }
      };
      if (this.opts.input) this.inputChannel = pc.createDataChannel("auoreo-input");
      await pc.setLocalDescription(await pc.createOffer());
      await waitGathering(pc);
      const answer = await this.api.rtcOffer({
        sdp: pc.localDescription?.sdp ?? "",
        session_id: this.opts.sessionId,
        input: Boolean(this.opts.input),
      });
      this.set({ rtcId: answer.rtc_id });
      await pc.setRemoteDescription({ type: "answer", sdp: answer.sdp });
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      await this.stop();
      this.set({ phase: "failed", error: msg });
    }
  }

  private wire(pc: RTCPeerConnection): void {
    pc.ontrack = (ev) => {
      if (ev.track.kind !== "audio") return;
      this.opts.onRemoteStream(ev.streams[0] ?? new MediaStream([ev.track]));
      this.set({ remoteAudio: true });
    };
    pc.oniceconnectionstatechange = () => {
      const ice = pc.iceConnectionState;
      const phase = ice === "connected" || ice === "completed" ? "connected" : ice === "failed" ? "failed" : this.s.phase;
      this.set({ ice, phase });
    };
    pc.onconnectionstatechange = () => {
      const dtls = pc.getSenders()[0]?.transport?.state;
      if (dtls) this.set({ dtls });
      if (pc.connectionState === "failed") this.set({ phase: "failed", error: "connection failed" });
    };
    const watchDtls = () => {
      const t = pc.getSenders().find((x) => x.transport)?.transport;
      if (!t) return;
      t.onstatechange = () => this.set({ dtls: t.state });
      this.set({ dtls: t.state });
    };
    pc.addEventListener("signalingstatechange", () => {
      if (pc.signalingState === "stable") watchDtls();
    });
  }

  setAudio(next: Partial<AudioControls>): void {
    const audio = { ...this.s.audio, ...next };
    if (this.mic) {
      this.mic.enabled = !audio.muted;
      if (next.suppressed !== undefined)
        void this.mic
          .applyConstraints({ echoCancellation: audio.suppressed, noiseSuppression: audio.suppressed })
          .catch(() => undefined);
    }
    this.set({ audio });
  }

  /** Camera or screen share on the pre-negotiated video sender (`replaceTrack`); "none" stops it. */
  async setVideo(source: VideoSource): Promise<void> {
    if (!this.pc || !this.videoSender) return;
    let track: MediaStreamTrack | null;
    let stream: MediaStream | null = null;
    try {
      if (source === "camera") stream = await navigator.mediaDevices.getUserMedia({ video: true });
      else if (source === "screen") stream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: false });
      track = stream?.getVideoTracks()[0] ?? null;
    } catch (e) {
      this.set({ error: e instanceof Error ? e.message : String(e) });
      return;
    }
    await this.videoSender.replaceTrack(track);
    this.videoTrack?.stop();
    this.videoTrack = track;
    if (track) track.onended = () => void this.setVideo("none"); // browser "Stop sharing"
    this.opts.onLocalVideo?.(stream);
    this.set({ video: track ? source : "none", error: null });
  }

  /** Send one teleop event on `auoreo-input`; false when the channel is not open. */
  sendInput(ev: InputEvent): boolean {
    const ch = this.inputChannel;
    if (!ch || ch.readyState !== "open") return false;
    ch.send(JSON.stringify({ id: `e${++this.seq}`, ...ev }));
    return true;
  }

  async stop(): Promise<void> {
    const id = this.s.rtcId;
    this.eventsChannel?.close();
    this.mic?.stop();
    this.videoTrack?.stop();
    this.pc?.getSenders().forEach((s) => s.track?.stop());
    this.pc?.close();
    this.pc = null;
    this.mic = null;
    this.videoTrack = null;
    this.videoSender = null;
    this.inputChannel = null;
    this.eventsChannel = null;
    this.opts.onLocalVideo?.(null);
    if (id) await this.api.rtcClose(id).catch(() => undefined);
    this.set({ phase: "closed", rtcId: null, ice: "closed", remoteAudio: false, video: "none" });
  }
}
