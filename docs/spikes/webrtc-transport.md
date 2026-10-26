# Spike 6: WebRTC transport (spec §22-6)

**Status:** decided and implemented as selectable `rtc_transport` backends; the loopback
voice/video path is tested, the WAN/CGNAT matrix is **not run here** (no UDP-blocked network,
no TURN host, no LiveKit Cloud project in the dev container).

## Options and what exists

| Option | In the tree | Notes |
|---|---|---|
| `aiortc` P2P | `backends/rtc_transport/aiortc` (default), `POST /api/rtc/offer`, `auoreo-events` data channel | in-process; loopback voice + video + barge-in (`response.cancel`) covered by integration tests |
| `livekit-agents` (self-host or Cloud) | `backends/rtc_transport/livekit` + `deploy/livekit/` | managed SFU / SIP; token minting and webhooks tested with fakes |
| OpenAI Realtime | `backends/rtc_transport/openai_realtime` | ephemeral `client_secrets` minted server-side (spec §19.12) |
| `pipecat-ai` | not adopted | would be a second pipeline framework next to the voice service |
| TURN | `deploy/coturn/` (TURN REST HMAC creds, `turn_ttl_s`) | ICE matrix row in doctor (`diagnostics_rtc.py`) |
| Signaling over tunnels | `cloudflared` / `tailscale` (`core/tunnels.py`) | signaling only; media needs UDP or TURN (serve prints this) |

## Measured here

Loopback only: offer/answer, data-channel events, VAD endpointing on a fake-mic tone, barge-in
cancel, echo guard. See `docs/rtc.md` and `docs/voice.md`.

## Not run here

- cost/latency report across aiortc vs LiveKit Cloud vs self-host SFU on a real WAN;
- CGNAT / symmetric NAT with coturn vs Cloud TURN;
- the UDP-blocked tunnel demo (signaling-only proof).

## Decision

`aiortc` stays the default for single-user loopback/LAN; LiveKit is the managed option for
WAN, SIP and multi-party. Revisit with the WAN numbers above.
