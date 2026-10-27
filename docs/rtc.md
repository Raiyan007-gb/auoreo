# WebRTC transport (M4e)

AUOREO accepts a WebRTC peer on the same port as the REST API. Signaling is one HTTP call
(`POST /api/rtc/offer`); the media and the `auoreo-events` data channel then run over
aiortc. The data channel carries the **same `ServerEvent` envelope** as SSE (`/api/events`)
and the WebSocket (`/ws`), so a client can use one event reducer for all three.

Install the optional extra first:

```bash
uv sync --extra rtc          # or: pip install "auoreo[rtc]"   (aiortc>=1.15,<2 + numpy)
```

Without it, `POST /api/rtc/offer` answers `501 {"detail": "install auoreo[rtc]"}`.

## Connecting from a browser

Signaling is non-trickle: wait for ICE gathering to finish, then send the whole offer.
The client must create the `auoreo-events` channel **before** `createOffer()` (an offer
with no data channel is rejected with 400).

```js
const token = "…";                                   // AUOREO_SERVER_PASSWORD
const pc = new RTCPeerConnection({ iceServers: [{ urls: "stun:stun.l.google.com:19302" }] });
const events = pc.createDataChannel("auoreo-events");

// Optional: send the microphone (server computes rtc.audio_level; STT comes later).
const mic = await navigator.mediaDevices.getUserMedia({ audio: true });
mic.getTracks().forEach((t) => pc.addTrack(t, mic));

await pc.setLocalDescription(await pc.createOffer());
await new Promise((r) => {
  if (pc.iceGatheringState === "complete") return r();
  pc.addEventListener("icegatheringstatechange", () => pc.iceGatheringState === "complete" && r());
});

const res = await fetch("/api/rtc/offer", {
  method: "POST",
  headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
  body: JSON.stringify({ sdp: pc.localDescription.sdp, type: "offer" /*, session_id */ }),
});
const { rtc_id, sdp, type } = await res.json();      // 201
await pc.setRemoteDescription({ sdp, type });

events.onmessage = (m) => {
  const frame = JSON.parse(m.data);
  if (frame.type === "connected") {
    events.send(JSON.stringify({ type: "subscribe", channels: [`session:${sessionId}`], last_seq: 0 }));
  } else if ("seq" in frame) {
    reduce(frame);                                  // a ServerEvent, same as SSE/WS
  }
};
```

Hang up with `DELETE /api/rtc/{rtc_id}` (idempotent; `{"closed": false}` if already gone)
or simply `pc.close()`; closing the `auoreo-events` channel also closes the peer.

## Endpoints (Bearer auth like the rest of `/api`)

| Method | Path | Result |
|---|---|---|
| `POST` | `/api/rtc/offer` `{sdp, type: "offer", session_id?}` | `201 {rtc_id, sdp, type: "answer"}`; 400 bad offer, 404 unknown session, 413 SDP > 64 KiB, 429 too many peers, 501 extra missing |
| `DELETE` | `/api/rtc/{rtc_id}` | `200 {closed: bool}` |
| `GET` | `/api/rtc` | `[{rtc_id, session_id, state, created_at}]` (`state` = `connectionState`) |

With `session_id` in the offer, the channel is auto-subscribed to `session:<id>` (live
only, from the moment the channel opens); send `subscribe` to change channels or replay.

## The `auoreo-events` channel

Server → client (one JSON object per message):

| Frame | When |
|---|---|
| `{"type":"connected","rtc_id":…,"server_seq":N}` | once, when the channel opens |
| `ServerEvent` (`id, seq, type, aggregate_id, aggregate_seq, data, created_at`) | replay and live tail of the subscribed channels, in `seq` order |
| `{"type":"pong","tick":n}` | reply to `ping` |
| `{"type":"error","subtype":…,"message":…}` | `unsupported`, `invalid_frame`, `too_large` (an event over 128 KiB carries its `seq`; fetch it from `GET /api/events`), `binary_dropped` |

Client → server:

| Frame | Meaning |
|---|---|
| `{"type":"subscribe","channels":["session:<id>","server"],"last_seq":N}` | same semantics as the `/ws` `subscribe`: replay `seq > N`, then live; omit `last_seq` for live only |
| `{"type":"ping","tick":n}` | keepalive |

Server-aggregate events emitted by the transport (subscribe to `server` to see them):

- `rtc.audio_level {rtc_id, session_id, rms, speaking}` — RMS (0..1) of the inbound audio
  resampled to 16 kHz mono, at most once per `rtc_audio_level_interval_s` (default 1 s);
  `speaking = rms > rtc_speaking_rms` (default 0.02).
- `rtc.closed {rtc_id, session_id, reason}` — `client`, `backpressure`, `channel_closed`,
  `failed`, `closed` or `shutdown`.

## What is WebSocket-only

Approvals, question answers, steering, interrupts and follow-ups **never** travel over the
data channel (spec §19.12). Any frame other than `subscribe`/`ping` gets
`{"type":"error","subtype":"unsupported","message":"control (approvals, steer, interrupt) is WebSocket-only"}`.
Use `/ws` (or the REST endpoints) for control and keep the data channel as a read mirror.

## Limits and posture

- SDP ≤ 64 KiB (413); data-channel messages ≤ 128 KiB both ways.
- At most `rtc_max_peers` (default 8) concurrent peers (429).
- Backpressure: above 1 MiB of `bufferedAmount`, coalescable events (`message.delta`,
  `tool.progress`, `ping`) are dropped; other events wait for the buffer to drain, and a
  peer that does not drain within 5 s is closed with reason `backpressure`. Reconnect and
  `subscribe` with your last `seq` to catch up.
- ICE: `rtc_ice_servers` (default `["stun:stun.l.google.com:19302"]`). On a host without
  UDP egress STUN gathering times out (~5 s per offer); set `"rtc_ice_servers": []` in
  the stored settings (Settings → Environment, or `AUOREO_RTC_ICE_SERVERS='[]'`) for loopback/LAN. TURN is not wired yet.
- SDP is never logged or audited: log lines show `[REDACTED sdp N bytes]`; audit rows
  `rtc.offer` / `rtc.close` carry only `{rtc_id, sdp_bytes, audio}` / `{rtc_id, reason}`.
- Media is DTLS-SRTP end to end. There is no outbound (TTS) track yet; a future STT
  backend plugs into `RtcManager.on_audio_frames` (see `src/auoreo/rtc/peers.py`).
