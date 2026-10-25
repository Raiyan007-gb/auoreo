# Remote plane (W2-B)

Spec P2 / M6: a UDS daemon, an exec-server for running commands on another machine,
instance bridges for team mail across AUOREO instances, and a multi-environment listing.
All of it is opt-in; a fresh install behaves exactly as before.

| Piece | Code | Entry point |
|---|---|---|
| Daemon | `src/auoreo/remote/{daemon,uds,cli_client}.py` | `auoreo daemon start\|stop\|status`, `auoreo serve --uds PATH` |
| Exec-server | `src/auoreo/remote/{exec_server,exec_local,rpc_client}.py` | `auoreo exec-server` |
| Remote environment | `backends/environment/remote/` | `settings.environment_remote` |
| Bridge | `src/auoreo/remote/bridge.py`, `api/routers/bridge.py` | `send_message(to="bridge:<peer>/<member>")`, `POST /api/bridge/inbox` |
| Listing | `src/auoreo/remote/environments.py` | `GET /api/environments`, `auoreo env list` |

## Daemon (Unix socket)

```bash
auoreo daemon start [--port 4096] [--gateway fake]   # detached; pid in <home>/daemon.pid
auoreo daemon status                                # /api/health over the socket (exit 1 if down)
auoreo run "hi"                                     # → goes over <home>/auoreo.sock
auoreo daemon stop                                  # SIGTERM, waits up to 10 s
```

- `start` re-executes `auoreo serve --uds <home>/auoreo.sock` with
  `start_new_session=True` (no controlling terminal, survives the shell), logs to
  `<home>/logs/daemon.log` and waits until the socket answers `/api/health`.
- The socket file is created 0600 (umask during `bind`) inside a 0700 directory. A stale
  socket is replaced; a live one or a non-socket file at that path is refused.
- **No token on the socket.** Only the owning user can `connect()` to a 0600 socket, so
  file permissions are the authentication. The UDS listener is wrapped in
  `TrustUnixSocket`, which presents the configured `server_password` to the normal auth
  dependency for requests whose ASGI `server` is `(path, None)` (uvicorn's UDS marker).
  `--port` additionally opens TCP, where bearer auth applies unchanged.
- **CLI auto-connect.** `remote.uds.daemon_socket(home)` is the single transport decision:
  if `<home>/auoreo.sock` answers `/api/health`, `auoreo run`, `auoreo session list` and
  `auoreo env list` use it (httpx `HTTPTransport(uds=…)`); otherwise they run in-process as
  before. `AUOREO_NO_DAEMON=1` forces in-process. Commands with `--url` also accept
  `--url unix:<path>`.
- Over the socket, `run` posts `/api/chat` and follows `/api/events?session_id=…` (SSE),
  so its output formats are unchanged; `--format json` adds `"transport": "unix:<path>"`.
  Difference: the daemon's runs are interactive (a tool that needs approval waits for one
  in the web UI / WS rather than being denied outright).
- Non-POSIX systems: `auoreo daemon` reports "unsupported" and exits 2.

## Exec-server

```bash
auoreo secrets set EXEC_TOKEN "$(python -c 'import secrets;print(secrets.token_urlsafe(32))')"
auoreo exec-server --port 7071 --token-secret secret:EXEC_TOKEN     # or AUOREO_EXEC_TOKEN=…
```

A small standalone Starlette app (not the main app): one WebSocket route `/rpc` speaking
JSON-RPC 2.0 with `ping`, `exec_command {session_id, cmd, workdir, tty, yield_time_ms,
max_output_tokens, env}`, `write_stdin {process_id, chars, yield_time_ms}` and
`close_session {session_id}`. It serves a local environment backend
(`remote/exec_local.py`: `/bin/sh -c` in its own process group, no pty; any
`EnvironmentBackend` can be injected instead).

Client side (`backends/environment/remote`, `remote = True`):

```json
{"environment_remote": {"url": "ws://127.0.0.1:7071", "token": "secret:EXEC_TOKEN"}}
```

`check()` is a `ping`. Use `ssh -L 7071:127.0.0.1:7071 host` or a TLS proxy (`wss://`) to
reach another machine; the server itself speaks plain WS.

### Threat model

Whoever holds the token can run commands as the exec-server's user. Controls:

1. **Token at the handshake.** `Authorization: Bearer <token>` is compared in constant time
   before `accept()`; anything else gets HTTP 403. A token is always required, even on
   loopback (other local users can reach loopback TCP). Browsers cannot set the header,
   so a web page cannot drive the server.
2. **Bind policy.** Non-loopback binds are refused unless `--allow-remote` *and* a token
   are given. Prefer loopback + SSH tunnel.
3. **Server-side floors.** The hardline `bash` floors from `core/permissions.py`
   (`rm -rf /`, `mkfs`, `dd of=/dev/…`, fork bombs, shutdown, privileged docker, …) plus
   any `--floor GLOB` are re-checked on the server, so a compromised client cannot bypass
   them. Everything above the floors (rules, modes, approvals) is the client's job.
4. **Scrubbed environment.** Commands get `core.secrets.child_env` (an allowlist) plus the
   client's variables minus loader hooks (`LD_*`, `DYLD_*`, `BASH_ENV`, …).
5. **Audit.** Every exec is logged with `cmd_hash` (sha256 prefix), connection, session
   and workdir — never the command text, which may carry secrets.
6. **Isolation between clients.** Sessions and processes are scoped to the WebSocket
   connection; another client cannot poll or kill them, and a dropped connection's
   processes are killed.

Not covered: the server runs unconfined as its user (combine with a container or a
dedicated account), and there is no TLS of its own.

## Instance bridge

```json
{
  "instance_name": "a",
  "bridges": {"b": {"url": "https://b.example:4096", "secret": "secret:BRIDGE_AB"}}
}
```

Peer B configures `bridges.a` with the same shared secret. An agent in a team on A calls
`send_message(to="bridge:b/worker", message=…)`.

Protocol (`POST <peer>/api/bridge/inbox`, body `BridgeEnvelope` in `schema/bridge.py`):

- Body: `{sender, from_member, to_member, team?, message, summary, nonce, timestamp}`.
- Headers: `X-Auoreo-Timestamp`, `X-Auoreo-Nonce` (must equal the body's) and
  `X-Auoreo-Signature: sha256=<hex HMAC-SHA256(secret, "<ts>.<nonce>." + raw body)>`.
- The receiver looks up `bridges[<sender>]` (unknown → 403), verifies the signature in
  constant time (401), enforces a ±300 s timestamp window (401) and refuses a nonce it has
  already seen (401). Only then is the mail delivered, into the newest local team that has
  `to_member` (or the team named by `team`), as `from: bridge:<sender>/<member>`.
- `uds:<socket path>/<member>` does the same over a local Unix socket without a signature
  (file permissions authenticate) and is delivered as `from: uds:<sender>/<member>`.
- Both sides audit (`bridge.send` / `bridge.receive`, allow and deny, without the body).
- The inbox is not behind the bearer token: the HMAC is its authentication.

## Sticky sessions

The nonce cache, live WebSocket connections, approvals and runs are per process. With
`--workers > 1` (Redis fan-out) put a sticky load balancer in front, and expect a replayed
bridge envelope to be caught only by the worker that saw the original — the 5-minute
timestamp window bounds that gap. The daemon itself is always a single worker.

## Multi-environment listing

`GET /api/environments` (and `auoreo env list`) returns `EnvironmentInfo` rows: discovered
`environment` backends (the selected one is `check()`ed), named environments from
`settings.environments` when that setting exists, the configured exec-server (pinged) and
each bridge peer (`/api/health`). URLs are stripped of credentials, query and fragment;
secrets are never returned.
