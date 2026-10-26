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
- `rtc:turn` — static TURN URLs and/or ephemeral TURN REST credentials (`turn_secret_ref` +
  `turn_urls`, F5); warns when neither is set or the referenced secret is missing.
- `rtc:transport` — the selected transport's `check()` (aiortc installed; LiveKit URL, keys,
  consent, SDK and reachability).

## LiveKit (P2, opt-in)

Nothing in P0–M5 depends on LiveKit; the SDK is the optional extra `auoreo[livekit]`
(`livekit-api`, plus the realtime SDK `livekit` for DTMF and spoken announcements, F5) and is
imported lazily.

```json
{
  "backends": {"rtc_transport": "livekit"},
  "livekit": {
    "url": "wss://sfu.example.net",
    "api_key_secret": "LIVEKIT_API_KEY",
    "api_secret_secret": "LIVEKIT_API_SECRET",
    "cloud_consent": false,
    "region": null,
    "sip": {"allowed_addresses": ["203.0.113.0/24"], "krisp_enabled": false,
            "outbound_trunk_id": "ST_xxx", "ivr": {"0": "transfer:+15550100", "9": "hangup"}}
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
- `krisp_enabled` (Krisp noise cancellation, on inbound trunks and outbound dials) defaults
  **off** since F5: on a self-hosted server it needs a Krisp-enabled build. `livekit.region`
  pins outbound SIP calls (`CreateSIPParticipant.destination.region`) and is echoed in the
  join answer's `extra.region`; for Cloud region pinning also use a regional URL.

### DTMF and IVR (F5)

The agent joins a call room through the realtime SDK (`livekit.rtc`) as identity `auoreo-agent`
with a token of kind `agent` (so `subscribable()` and other agents skip it) and listens for
`sip_dtmf_received`: livekit-sip relays RFC 2833/4733 telephone-events as `SipDTMF`. Each digit
emits `rtc.dtmf{room, participant, digit, action}`. `livekit.sip.ivr` maps digits to actions:

| Action | Effect |
|---|---|
| `transfer:<+E164 \| tel: \| sip:>` | cold transfer of the pressing participant (dial tone on) |
| `say:<text>` | TTS through the voice service, published as a one-off audio track |
| `hangup` | `RemoveParticipant` for the caller (audited `rtc.hangup`) |
| `followup:<text>` | `[caller X pressed D] <text>` queued as a follow-up turn of the room's session |

A failing action emits `rtc.ivr_failed` instead of crashing the listener. Outbound tones:
`send_dtmf(room, "123#")` publishes `publish_dtmf(code, digit)` per tone (codes per RFC 4733:
`*`=10, `#`=11, `A`–`D`=12–15; `w` = 0.5 s pause); the audit row has the tone count, never the
digits (they are often PINs). Without the realtime SDK, `send_dtmf`/`connect_agent` raise
"realtime SDK is not installed" (HTTP 501 through the API) and nothing listens for DTMF.

### Transfers (F5): `call_transfer` tool

`call_transfer` (deferred; permission `telephony`, default `ask`, destructive) refuses unless
`settings.backends.rtc_transport` is `livekit`. `caller` is the SIP participant identity;
`room` defaults to the room joined for the current session.

- **cold** — SIP REFER (`TransferSIPParticipant`) with `play_dialtone` (default true in the tool,
  false in the raw helper = room audio). The carrier trunk must allow transfers.
- **warm** — dial the target with `CreateSIPParticipant` (outbound trunk
  `livekit.sip.outbound_trunk_id`, `wait_until_answered`, 30 s ringing timeout, SRTP REQUIRE,
  Krisp/region from settings), speak `announce` to them, bridge, then the agent leaves.
  *Cloud / Private Cloud:* the consult happens in a separate room `<room>-consult-<id>` and the
  target is moved into the caller's room with `MoveParticipant` (only Cloud implements it).
  *Self-hosted:* the target is dialled straight into the caller's room (`bridge: in_room`), so
  the caller hears the announcement too. Without TTS/realtime SDK the announcement is skipped
  (`announced: false`), not failed.

Both emit `rtc.transfer{room, kind: warm|cold, caller, target, …}` and are audited
`rtc.transfer`; `target` is masked to its last four digits in events and audit rows.

### Self-hosting (F5): `deploy/livekit/`

`deploy/livekit/compose.livekit.yaml` runs livekit-server, livekit-sip, livekit-egress and Redis
with host networking (see [`deploy/README.md`](../deploy/README.md)). Requirements from spec
§26.8: a domain with a real CA certificate for the API, a **separate** TURN domain for the
embedded TURN/TLS on 5349 (443 without a load balancer), tcp 7881, udp 50000–60000, SIP 5060
(udp+tcp; 5061 TLS) and RTP udp 10000–20000; egress wants ≥ 4 CPU / 4 GB per worker and
`SYS_ADMIN`. Keys come only from `${LIVEKIT_API_KEY}` / `${LIVEKIT_API_SECRET}`.

## Ephemeral TURN for the aiortc path (F5)

LiveKit brings its own TURN. For the P2P aiortc transport, run the opt-in coturn in
`deploy/coturn/` (`use-auth-secret`, relays never to RFC 1918 / loopback / link-local / CGNAT
ranges, `no-multicast-peers`, `no-tcp-relay`) and configure:

```json
{"turn_secret_ref": "secret:TURN_SECRET", "turn_urls": ["turns:turn.example.com:5349"], "turn_ttl_s": 3600}
```

`GET /api/rtc/ice` then answers `{iceServers, expires_at, ttl_s}`: the static `rtc_ice_servers`
plus one entry with TURN REST credentials (`username = "<expiry>:auoreo"`, `credential =
base64(HMAC-SHA1(secret, username))`), `Cache-Control: no-store`. The secret is never returned;
the server-side aiortc peer uses the same derived credentials. Clients should fetch it right
before each offer (the bundled SPA does not open WebRTC peers itself yet, so there was no SPA
config spot to change).

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
  API calls (room, SIP, egress, MoveParticipant, CreateSIPParticipant) ran against a fake
  client. No call was placed, no egress file was written, and Cloud was never contacted.
- DTMF / IVR / announcements: a fake `livekit.rtc.Room` emitted `sip_dtmf_received`; the real
  realtime SDK (`livekit` 1.1) is installed but never connected to a server, and the PCM
  publishing path (`AudioSource`/`LocalAudioTrack`) was not run against a room.
- Warm transfer: request sequence only; whether a carrier accepts the outbound leg, and
  `MoveParticipant` on Cloud, are untested.
- `deploy/`: YAML structure and `docker compose config` only; no container was started (no
  Docker daemon), the config keys were checked against LiveKit's documented samples, and the
  livekit-sip env-var key fallback (`LIVEKIT_API_KEY`) follows upstream's config, not a run.
- coturn / ephemeral TURN: the credential algorithm is checked against an `openssl` HMAC vector;
  no coturn server validated a credential.
- aiortc join: real loopback offer/answer through the API; no NAT traversal.
- Teleop input: aiortc loopback against the `fake` desktop backend; no real display was driven.
- STUN: the doctor probe was tested against a local UDP responder; public STUN only in live runs.

## I8: LiveKit worker, token endpoint, webhook, web toggle

- **Worker** (`auoreo.voice.livekit_worker`, `{"i8": {"livekit": {"worker": {"enabled": true}}}}`
  with `backends.rtc_transport = "livekit"`): an in-process `livekit-agents` `AgentServer`
  (thread jobs, explicit dispatch `agent_name`, default `auoreo`). Each job joins the room with an
  `AgentSession` whose STT is a tap feeding the participant's 16 kHz audio into a `LiveKitLink`
  peer of the `RtcManager`, so transcription, barge-in, echo guard, filters and spoken replies are
  the same code and events as aiortc; replies are published as track `auoreo-voice`; barge-in calls
  `AgentSession.interrupt()`. `livekit-agents` is **not** a declared extra (every 1.x pins
  OpenTelemetry/OpenAI versions that conflict with the `otel` extra and LiteLLM): install it by
  hand; `status` and the doctor row `livekit:worker` say so.
- **`POST /api/livekit/token`** (AUOREO auth; LiveKit standard token-endpoint body + `session_id`):
  `201 {server_url, participant_token, room_name, participant_identity, expires_in}`. Identity is a
  server uuid4 (a client value is ignored), the room must be one this server created for the
  session (else a new uuid room), grants are `room_join` + publish/subscribe only, TTL
  `livekit.token_ttl_s` ≤ 900 s, `room_config` accepts only dispatch to the worker's agent,
  metadata/attributes ≤ `i8.livekit.max_metadata_bytes`. Audited with hashes, never the JWT.
- **`POST /livekit/webhook`** (no AUOREO auth; called by the LiveKit server): the `Authorization`
  JWT (API secret) must carry `sha256` of the raw body (`hmac.compare_digest`); no secret → 403,
  bad signature → 401, a redelivered event id within 1 h → 200 `duplicate`, more than
  `i8.livekit.webhook_rate_per_min` (30) → 429. Emits `livekit.webhook{event, room_hash, …}`.
- **Web**: `GET /api/rtc/capabilities` reports `livekit` / `livekit_agent`; the call panel then
  shows a **LiveKit** switch that joins via `livekit-client` (loaded lazily) instead of P2P.
  Teleop input and approvals never use LiveKit data channels.

The loopback contract (speech → transcript → spoken reply → barge-in cut → bounds) runs on both
transports in `tests/integration/test_i8_transports.py` (aiortc for real, LiveKit through fake
`livekit.rtc` / `livekit.agents` namespaces; no LiveKit server here).

## I8: computer use safety and web computer use

`computer` tool (`auoreo.tools.cu_safety`, `i8.computer_use`):

- `[input_target_mismatch]`: a mutating op is refused when the focused window `{pid, window_id}`
  changed since the last screenshot (outside the agent's own actions); take a new screenshot.
- `[stale_element_token]`: element tokens from a `som`/`ax` screenshot are valid until the next
  mutating op, unless `capture_after: true` re-read the state.
- Sticky `pid` + `window_id` target later ops until another window or `focus_app`.
- Verdict enum on every mutating op: `confirmed → done`, `unverifiable → verify_fresh_state`,
  `suspected_noop → escalate`.
- New ops `middle_click` (alias `middle`), `set_value` (token + `text`), `list_apps`; `mode`
  `som | vision | ax`; screenshots capped at `max_image_dimension` (1456 px); pointer ops wait
  `move_settle_ms` (50 ms); `type` of ≥ 200 or non-ASCII chars pastes via the clipboard (save →
  set → verify → paste → 100 ms → restore); at the end of a run `unhide()` + lock release.
- `health_report()` → doctor row `desktop:health` (exit 0/1/2, `display_count=0` is unavailable,
  macOS needs Accessibility + Screen Recording for cua-driver). `no_overlay` defaults per OS (off
  on macOS/X11/headless/WSL2, on on Windows/Wayland); override `i8.computer_use.no_overlay`.

Web: `i8.browser.trace` records a Playwright trace per session at
`<home>/sessions/<id>/browser-trace-<ts>.zip`. `backends.browser = "chrome_cdp"` attaches to a
Chrome you started with `--remote-debugging-port` (`i8.browser.cdp_url`, loopback only) — real
profile, so every session first needs the `browser_cdp` permission (ask by default).

Not tested here: a real desktop / cua-driver, macOS permissions, a user's own Chrome profile (a
locally launched headless Chromium stands in for the CDP test).
