# deploy/

Self-hosted media servers for AUOREO (F5, spec §19.12 and §26.8). The AUOREO service itself is
deployed with the root `compose.yaml` / `Dockerfile`. Nothing here contains secrets: every
credential is interpolated from the environment, and compose refuses to start without it.

| Path | What | When |
|---|---|---|
| `livekit/livekit.yaml` | livekit-server (SFU + embedded TURN) | `backends.rtc_transport = "livekit"` |
| `livekit/sip.yaml` | livekit-sip (SIP trunks, DTMF, transfers) | telephony |
| `livekit/egress.yaml` | livekit-egress (audio-only recordings) | recordings |
| `livekit/compose.livekit.yaml` | all three + Redis | |
| `coturn/turnserver.conf`, `coturn/compose.coturn.yaml` | coturn with TURN REST auth | opt-in, aiortc P2P path only |

## LiveKit (self-hosted is the default; Cloud only with `livekit.cloud_consent`)

```bash
export LIVEKIT_API_KEY=APIxxxxxxxx LIVEKIT_API_SECRET=$(openssl rand -base64 32)
mkdir -p deploy/livekit/certs   # turn.crt/key (TURN domain), sip.crt/key; a real CA cert
docker compose -f deploy/livekit/compose.livekit.yaml up -d
auoreo secrets set LIVEKIT_API_KEY && auoreo secrets set LIVEKIT_API_SECRET
```

`settings.json`: `{"backends": {"rtc_transport": "livekit"}, "livekit": {"url": "wss://lk.example.com"}}`.
Terminate TLS for `lk.example.com` → `127.0.0.1:7880` in your reverse proxy.

Every service uses `network_mode: host` (Linux): WebRTC and RTP need large UDP ranges that
docker-proxy handles badly, and host networking keeps ICE candidate addresses correct. Open:

| Port | Proto | Purpose |
|---|---|---|
| 7880 | tcp | API / WebSocket (behind TLS) |
| 7881 | tcp | ICE/TCP fallback |
| 50000–60000 | udp | ICE media |
| 3478 | udp | embedded TURN/UDP + STUN |
| 5349 | tcp | embedded TURN/TLS on a **separate domain** (`turn.domain`; use 443 without an L4 LB) |
| 5060 | udp+tcp | SIP signalling (limit to carrier IPs) |
| 5061 | tcp | SIP over TLS |
| 10000–20000 | udp | SIP RTP media |

Redis binds to 127.0.0.1 only. Egress runs with `cap_add: [SYS_ADMIN]` (Chrome sandbox) and
wants **≥ 4 CPU / 4 GB per worker**: scale by adding workers on more hosts. No REGISTER and no
video over SIP; AUOREO creates trunks with SRTP `REQUIRE`.

## coturn (opt-in, aiortc only)

LiveKit already embeds TURN; use coturn only for the P2P aiortc transport.

```bash
export TURN_SECRET=$(openssl rand -hex 32)
docker compose -f deploy/coturn/compose.coturn.yaml up -d
TURN_SECRET=$TURN_SECRET auoreo secrets set TURN_SECRET   # or keyring / secrets.db
```

`settings.json`: `{"turn_secret_ref": "secret:TURN_SECRET", "turn_urls": ["turns:turn.example.com:5349"]}`.
AUOREO derives short-lived credentials (TTL `turn_ttl_s`, default 1 h) for `GET /api/rtc/ice`
and the server-side peer; the secret never leaves the server. Edit `realm`/`server-name` and
the relay range (`min-port`/`max-port`, udp) for your host. The config denies relaying to
private, loopback, link-local and CGNAT ranges. coturn does not expand env vars in its config
file, so the secret is passed as `--static-auth-secret` by compose (visible to local root via
`docker inspect`; use a dedicated host).

## Validating without a daemon

```bash
LIVEKIT_API_KEY=k LIVEKIT_API_SECRET=s docker compose -f deploy/livekit/compose.livekit.yaml config -q
TURN_SECRET=t docker compose -f deploy/coturn/compose.coturn.yaml config -q
uv run pytest tests/unit/test_deploy_configs.py
```
