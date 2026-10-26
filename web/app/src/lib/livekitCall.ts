/**
 * LiveKit call client (spec §26.9, I8): the alternative transport behind the call panel toggle.
 *
 * - The token comes from `POST /api/livekit/token` (server-minted, uuid identity, ≤ 15 min);
 *   the browser never holds the API secret.
 * - `livekit-client` is imported lazily, so the P2P path does not load it.
 * - Mic/camera/screen use the `LocalParticipant` controls (`setMicrophoneEnabled`, …).
 * - The AUOREO voice plane answers through the in-process worker; transcripts and barge-in
 *   events still arrive on the durable `/api/events` stream. Approvals never travel over
 *   LiveKit data/RPC: they stay on `/ws`.
 * Same public surface as `RtcCall` so `CallPanel` can hold either.
 */
import type { LiveKitToken, LiveKitTokenRequest } from "@sdk/client";
import { type AudioControls, type CallSnapshot, IDLE_CALL, type InputEvent, type VideoSource } from "@/lib/rtc";

export interface LiveKitApi {
  livekitToken: (b: Partial<LiveKitTokenRequest>) => Promise<LiveKitToken>;
}

export interface LiveKitCallOptions {
  readonly sessionId: string;
  readonly agentName?: string;
  readonly onChange: (s: CallSnapshot) => void;
  readonly onRemoteStream: (s: MediaStream) => void;
  readonly onLocalVideo?: (s: MediaStream | null) => void;
}

type LkRoom = import("livekit-client").Room;

export class LiveKitCall {
  private room: LkRoom | null = null;
  private s: CallSnapshot = IDLE_CALL;

  constructor(
    private readonly api: LiveKitApi,
    private readonly opts: LiveKitCallOptions,
  ) {}

  get snapshot(): CallSnapshot {
    return this.s;
  }

  private set(patch: Partial<CallSnapshot>): void {
    this.s = { ...this.s, ...patch };
    this.opts.onChange(this.s);
  }

  async start(): Promise<void> {
    this.set({ ...IDLE_CALL, phase: "connecting" });
    try {
      const lk = await import("livekit-client");
      const tok = await this.api.livekitToken({
        session_id: this.opts.sessionId,
        room_config: this.opts.agentName ? { agents: [{ agent_name: this.opts.agentName }] } : null,
      });
      const room = new lk.Room({ adaptiveStream: true, dynacast: true });
      this.room = room;
      room
        .on(lk.RoomEvent.TrackSubscribed, (track) => {
          if (track.kind !== lk.Track.Kind.Audio || !track.mediaStreamTrack) return;
          this.opts.onRemoteStream(new MediaStream([track.mediaStreamTrack]));
          this.set({ remoteAudio: true });
        })
        .on(lk.RoomEvent.ConnectionStateChanged, (state) => {
          const phase = state === lk.ConnectionState.Connected ? "connected" : state === lk.ConnectionState.Disconnected ? "closed" : "connecting";
          this.set({ phase, ice: phase === "connected" ? "connected" : "checking" });
        })
        .on(lk.RoomEvent.Disconnected, (reason) => {
          if (this.s.phase !== "closed") this.set({ phase: "closed", error: reason ? `disconnected (${String(reason)})` : null });
        });
      await room.connect(tok.server_url, tok.participant_token);
      await room.localParticipant.setMicrophoneEnabled(true, { echoCancellation: true, noiseSuppression: true, autoGainControl: true });
      this.set({ rtcId: tok.room_name, phase: "connected", ice: "connected", dtls: "connected" });
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      await this.stop();
      this.set({ phase: "failed", error: msg });
    }
  }

  setAudio(next: Partial<AudioControls>): void {
    const audio = { ...this.s.audio, ...next };
    if (this.room && next.muted !== undefined) void this.room.localParticipant.setMicrophoneEnabled(!audio.muted).catch(() => undefined);
    this.set({ audio });
  }

  async setVideo(source: VideoSource): Promise<void> {
    const p = this.room?.localParticipant;
    if (!p) return;
    try {
      await p.setCameraEnabled(source === "camera");
      await p.setScreenShareEnabled(source === "screen");
    } catch (e) {
      this.set({ error: e instanceof Error ? e.message : String(e) });
      return;
    }
    this.opts.onLocalVideo?.(null);
    this.set({ video: source, error: null });
  }

  /** Teleop input stays on the P2P `auoreo-input` channel; LiveKit data never carries control. */
  sendInput(ev: InputEvent): boolean {
    void ev;
    return false;
  }

  async stop(): Promise<void> {
    const room = this.room;
    this.room = null;
    if (room) await room.disconnect().catch(() => undefined);
    this.opts.onLocalVideo?.(null);
    this.set({ phase: "closed", rtcId: null, ice: "closed", remoteAudio: false, video: "none" });
  }
}
