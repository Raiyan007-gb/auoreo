# Spike: HTTP/3 (spec L1043, 2 h maturity check)

**Decision:** keep **uvicorn** as the only server inside AUOREO and add no new dependency.
Note that uvicorn speaks HTTP/1.1 only, not h2. When HTTP/3 (or HTTP/2) matters, terminate it
at the edge: Caddy, using `deploy/caddy/Caddyfile`, or a CDN in front of `:4096`. Hypercorn
can serve the app over HTTP/3 (the probe below confirmed it), but it would add a second server
stack (hypercorn + aioquic) for no measured gain on the paths AUOREO cares about.

## What was measured (this container)

Tools: `scripts/spikes/http3_probe.py` (run with `uv run --with 'hypercorn[h3]'`; not a
project dependency) and `scripts/spikes/http3_caddy_e2e.sh`. Versions: hypercorn 0.18.0,
aioquic 1.7.0, uvicorn 0.54.0, Caddy 2.10.2 (release binary downloaded to a scratch dir),
Python 3.13.17. Everything ran on loopback with a fresh `AUOREO_HOME` and the fake gateway.

| Check | Hypercorn serving the app directly (TCP+TLS `bind` + UDP `quic_bind`) | Caddy h3 edge → uvicorn h1 (`deploy/caddy/Caddyfile`) |
|---|---|---|
| `GET /api/health` over **HTTP/3** | ✅ 200, `server: hypercorn-h3` | ✅ 200 (`server: uvicorn` behind Caddy) |
| **SSE** `/api/events` over HTTP/3: `: connected` comment, then a live `session.created` caused by `POST /api/sessions` on another h3 stream | ✅ | ✅ (flushed immediately) |
| **WebSocket** `/ws` over HTTP/3 (RFC 9220 extended CONNECT, `:protocol websocket`) | ✅ 200 + the `connected` frame | ❌ **405**: Caddy does not accept WebSocket over h3 |
| WebSocket `wss://…/ws` over TCP (HTTP/1.1 Upgrade, what browsers use) | ✅ | ✅ |
| `alt-svc` advertised on the TCP side | `h3=":<udp>"; ma=3600` | `h3=":18443"; ma=2592000` |
| TCP side negotiated | HTTP/2 | HTTP/2 |
| 200 × `/api/health`, h3, median / p95 | 1.67 / 1.97 ms | 1.31 / 1.57 ms |
| 200 × `/api/health`, h2, median / p95 | 1.52 / 1.92 ms | 1.73 / 1.98 ms |

The Caddy column is from a run on a quiet machine. An earlier run, overlapping a Nuitka build
and a `cargo build`, measured h3 1.59 / 4.63 ms and h2 1.84 / 4.98 ms with the same
functional results. One more run timed out on the QUIC handshake (`TimeoutError`; h2 and wss on
the same Caddy were fine). The cause was not isolated. Processes left over from the previous
run were still alive (`uv run` does not pass the kill on to its child, so the script now also
`pkill`s the server). After cleaning them up, the rerun passed.

Other findings:

- `curl --http3` is **not available** here: curl 8.5.0 links OpenSSL 3.0 without
  nghttp3/ngtcp2 ("the installed libcurl version doesn't support this"). The probe therefore
  uses its own aioquic client.
- `aioquic.asyncio.connect` always binds an AF_INET6 dual-stack socket and fails with
  `EAFNOSUPPORT` where IPv6 is disabled, as in this container. The probe binds an IPv4 datagram
  endpoint itself. That is a client-side papercut, not a server one.
- On loopback, h3 is no faster than h2 (within ±0.2 ms on the median). HTTP/3 pays off on
  lossy or roaming networks (no TCP head-of-line blocking, connection migration). Loopback
  cannot show that, and AUOREO's heavy traffic is long-lived SSE/WS streams, not many small
  requests.

## Options

| Option | Pros | Cons |
|---|---|---|
| A. uvicorn only (status quo) | one stack, already tested; SSE and WS proven | HTTP/1.1 only |
| B. Hypercorn as an optional server (`auoreo serve --server hypercorn --quic`) | h1/h2/h3 in-process, WS over h3 works | a second server to test and support (lifespan, `--workers`, UDS, signal handling all differ); aioquic in the binary; UDP 443 plus a certificate in AUOREO; no browser uses WS over h3 today anyway |
| **C. uvicorn + edge proxy (Caddy / CDN) for h2/h3** | automatic TLS, h3 + h2 at the edge, zero AUOREO code; SSE verified over h3 end to end | one more process when exposing beyond localhost; WS stays on h1/h2 (fine: browsers do that) |

## Decision

**Option C.** `deploy/caddy/Caddyfile` ships as the reference edge:

- `servers { protocols h1 h2 h3 }`.
- `reverse_proxy` to `127.0.0.1:4096` with `flush_interval -1`, so SSE and any chunked stream
  are flushed immediately.
- `stream_close_delay 5m`, so WebSockets survive `caddy reload` instead of all reconnecting
  at once.
- Compression on, except for `/api/events`, `/ws`, `/mcp*` and `/acp*`.
- Long cache on `/assets/*`.
- Every port, the domain and the upstream can be overridden by environment variable for a
  rootless local test.

`caddy validate` accepts it, and `scripts/spikes/http3_caddy_e2e.sh` drives it end to end as
measured above. AUOREO stays on loopback with `AUOREO_SERVER_PASSWORD` set; Caddy adds no auth.

## Follow-ups

- Revisit option B only if a client needs h3 *to AUOREO itself* without a proxy (for example
  a mobile app over a flaky network straight to a home server) and uvicorn still has no h2/h3.
- If WebSocket over h3 ever matters: Caddy returns 405 for RFC 9220 today. Track Caddy and
  browser support.
- The compose file could gain an opt-in `caddy` service using this Caddyfile (owned by the
  deploy workstream).
