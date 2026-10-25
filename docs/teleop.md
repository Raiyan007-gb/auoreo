# Teleop, RTC transports and IoT connectors (W2-D)

AUOREO keeps **signaling** on its one port (default 4096) and lets **media** take the best path
available. Control (approvals, steer, interrupt) never travels over a media data channel: it
stays on `/ws` and the REST API.

## Signaling: through the tunnel on :4096

- `POST /api/rtc/join` is transport-agnostic. The selected `rtc_transport` backend
  (`settings.backends.rtc_transport`, default `aiortc`) answers with either an SDP answer
  (P2P) or a room URL + access token (SFU). `DELETE /api/rtc/join/{join_id}` leaves.
- The M4e routes are unchanged: `POST /api/rtc/offer`, `DELETE /api/rtc/{rtc_id}`,
  `GET /api/rtc`. `join` with the aiortc transport uses the same `RtcManager`, so peers from
  either route show up in `GET /api/rtc`.
- Remote access goes through the existing tunnels (Tailscale / Cloudflare / ngrok) to :4096.
  Only signaling and the API use the tunnel; media does not.
- SDP and tokens are never logged or audited (`rtc.join` audit rows carry transport, join id
  and room only).

## Media: direct, TURN, or a LiveKit SFU

| Path | When it works | Configure |
|---|---|---|
| Host candidates | Same LAN / loopback | `rtc_ice_servers: []` |
| STUN (server-reflexive) | Both sides behind "easy" NATs | `stun:host:3478` (default: Google STUN) |
| TURN relay | Symmetric NAT, **CGNAT** (mobile carriers, Starlink, many ISPs), strict firewalls | `turn:` / `turns:host:443?transport=tcp` in `rtc_ice_servers` |
| LiveKit SFU | Many viewers, phones (SIP), recording; server has a public address | `backends.rtc_transport = "livekit"` + `settings.livekit` |

CGNAT notes: a robot or laptop on a mobile hotspot usually sits behind carrier-grade NAT,
where STUN finds a public address that inbound packets still cannot reach. Plan for a TURN
server (coturn) reachable on UDP 3478 and TCP/TLS 443, or a LiveKit server with a public IP
(it embeds a TURN server). `auoreo doctor` shows which you have.

### `auoreo doctor` rows

- `rtc:ice` — the configured STUN/TURN URLs (query strings stripped; credentials never shown).
- `rtc:stun` — a RFC 5389 Binding request to the first `stun:` server over UDP with a 2 s
  timeout, reporting your public mapped address. With `AUOREO_OFFLINE=1` it is skipped and
  says so; no answer means offline or UDP blocked.
- `rtc:turn` — whether any TURN server is configured (warns: CGNAT peers will fail without one).
- `rtc:transport` — the selected transport's `check()` (aiortc installed; LiveKit URL, keys,
  consent, SDK and reachability).

## LiveKit (P2, opt-in)

Nothing in P0–M5 depends on LiveKit; the SDK is the optional extra `auoreo[livekit]`
(`livekit-api`) and is imported lazily.

```json
{
  "backends": {"rtc_transport": "livekit"},
  "livekit": {
    "url": "wss://sfu.example.net",
    "api_key_secret": "LIVEKIT_API_KEY",
    "api_secret_secret": "LIVEKIT_API_SECRET",
    "cloud_consent": false,
    "sip": {"allowed_addresses": ["203.0.113.0/24"], "krisp_enabled": true}
  }
}
```

- API key and secret come from the secret store (`auoreo secrets set LIVEKIT_API_KEY`), never
  from settings.
- `join` creates room `auoreo-<session_id>` and mints a token with `room_join` for that room
  only (no admin/create/record grants), identity = the request's `identity`, TTL 1 h.
- **Self-hosted is the default.** A `*.livekit.cloud` URL is refused (doctor warns, `join`
  answers 403) until `cloud_consent: true` is set: with Cloud, media and room metadata leave
  your network and are processed by a third party.
- Egress uses only `StartEgress`; the audio recording helper sends
  `TemplateSource{audio_only: true}` to one OGG file. Participants of kind EGRESS, INGRESS and
  AGENT are skipped by any subscription logic (`subscribable()`), so the agent never listens
  to recorders or other agents.

### SIP security

- Inbound: a trunk plus a dispatch rule (`SIPDispatchRuleIndividual`, room prefix
  `auoreo-call-`, one room per caller). A trunk with `numbers=[]` accepts calls for any number,
  so the helper **refuses** it unless `allowed_addresses` (carrier IPs) or digest auth
  (username + password from the secret store) is set.
- Media encryption is always `SIP_MEDIA_ENCRYPT_REQUIRE` (SRTP); the outbound trunk uses TLS
  transport. No REGISTER (the carrier points the number at the trunk), no video.
- `krisp_enabled` (noise cancellation) defaults on. DTMF reaches room participants as SIP DTMF
  data packets (nothing in AUOREO consumes them yet); cold transfer is SIP REFER (`transfer(room, identity, "tel:+1…")`). Warm transfer (consult first,
  then bridge) is not implemented here.

## Smart home: connector plugins (P2)

Sensors and devices are never core: they are out-of-process plugins with their own venvs.

- `plugins/mqtt` — paho-mqtt v2 (`CallbackAPIVersion.VERSION2`, `loop_start()`,
  `clean_session=False` so QoS 1/2 subscriptions survive reconnects). Messages on the
  configured `subscribe` filters become connector messages (JSON `{topic, payload ≤ 4 KiB as
  utf-8 or base64, retained, qos}`) routed to one session per filter (or per topic). Topics
  outside the allowlist are dropped even when a persistent broker session still delivers them.
  `mqtt_publish` only writes to `publish_allow` filters and asks for approval unless a rule
  allows `plugin__mqtt__mqtt_publish`. Payloads never appear in logs.
- `plugins/miio` — Xiaomi miIO (`miio_info`, `miio_call`, approval by default). Tokens only from
  the secret store (`MIIO_TOKEN_<DEVICE>`), never in results or errors.

Both ship `enabled: false`; enable them in `plugin.yaml` (or an overlay) and configure
`settings.plugins.mqtt` / `settings.plugins.miio`.

## Remote input: the `auoreo-input` channel (F1, "teleop beyond view")

Off by default. With `settings.teleop_input_enabled: true`, an authenticated client opens the
peer with `{"sdp": …, "session_id": "ses_…", "input": true}` and creates a data channel
labelled `auoreo-input` (next to `auoreo-events`). Without the setting the offer is refused
with **403**, and on peers opened without `input: true` the channel is ignored.

Frames are JSON, coordinates relative 0–1000 like the `computer` tool:

```json
{"id": "e1", "op": "click", "x": 500, "y": 500}
{"id": "e2", "op": "type", "text": "hello"}
{"id": "e3", "op": "key", "key": "ctrl+s"}
{"id": "e4", "op": "scroll", "x": 500, "y": 500, "direction": "down", "amount": 3}
```

`move` is accepted by the protocol but acked `ok: false`: the `computer` tool has no hover op.

**Approvals path.** Every event becomes one `computer` tool call through the normal executor
in the peer's session, `interactive=True`: floors (lock/logout/kill combos, download-to-shell
text), Starlark policies, permission rules, hooks and audit all apply exactly as for the
agent. An `ask` decision is an ordinary `permission.requested` on SSE `/api/events` and `/ws`,
answered with `POST /api/permissions/{id}/reply` (an `always` grant then covers that op for
the session). Nothing about the approval travels over the data channel (§19.12); the
channel only gets the ack once the call finished:

```json
{"id": "e1", "ok": true, "detail": "click at 500,500 [background]: ok - click done"}
```

Events run one at a time in arrival order. At most 30 events per rolling second per peer;
the rest are dropped silently and counted. Each accepted event is audited as
`teleop.input{rtc_id, op}` — typed text only as `text_chars`, never the text.

## What was not tested here

There was no MQTT broker hardware, Xiaomi device, LiveKit server, SIP carrier or TURN server in
this environment.

- MQTT: tested with real paho-mqtt clients against a minimal in-process MQTT 3.1.1 broker
  (`tests/mqtt_broker.py`), not Mosquitto/EMQX; TLS and broker auth failures were not exercised.
- miIO: tested with a fake `miio` module only; python-miio itself was not installed or run.
- LiveKit: token minting and the protobuf request shapes are real (`livekit-api`); the server
  API calls (room, SIP, egress) ran against a fake client. No call was placed, no egress file
  was written, and Cloud was never contacted.
- aiortc join: real loopback offer/answer through the API; no NAT traversal.
- Teleop input: aiortc loopback against the `fake` desktop backend; no real display was driven.
- STUN: the doctor probe was tested against a local UDP responder; public STUN only in live runs.
