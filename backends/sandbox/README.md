# backends/sandbox/

Process confinement for tools that run model-supplied commands (`bash`, the persistent
shells). ABC: `src/auoreo/backends/sandbox.py` — `run(ExecSpec) -> ExecResult`,
`wrap_long_lived(ExecSpec, tty=) -> (argv, enforced) | None`, `capabilities()`, `check()`.

| backend | default | enforces | needs |
|---|---|---|---|
| `none` | yes | nothing (`enforced=False`; the model is told once per session) | — |
| `docker` | no | fs + net (cap_drop ALL, no-new-privileges, uid 1000, pids/mem limits, `network_mode=none` for `net: none`) | `uv sync --extra sandbox-docker` + a daemon |
| `bwrap` | no | fs + net (ro `/usr`, private `/proc` `/dev` PID ns, `--unshare-net` for `net: none`) | `bwrap` on PATH; Landlock layered inside when the kernel has it (ABI in `check()`) |
| `seatbelt` | no | fs + net (`/usr/bin/sandbox-exec -p <SBPL>`: deny default, ro system, rw cwd + tmp, loopback-only for `net: proxy`) | macOS |
| `fake` | no | claims fs + net, runs nothing: records every `ExecSpec` in `calls`, answers from the `responses` script (default: exit 0, command text on stdout), refuses long-lived processes | tests and CI only (`settings.backends.sandbox = "fake"`); never auto-probed |

`none` + `security.sandbox_landlock: true` runs commands under the Landlock helper (fs only).

Select with `settings.backends.sandbox`. Unset (or `"auto"`) probes the strongest local
backend: `bwrap` on Linux, `seatbelt` on macOS; only when none is usable does it run `none`
(warning + `sandbox.fallback` audit row + doctor warn; `sandbox_policy: fail` refuses).
Persistent shells (`exec_command`/`write_stdin`, `terminal_pty`, `monitor`'s streaming `cmd`)
spawn **inside** the selected backend through `SandboxBackend.wrap_long_lived(spec, tty=)`:
`bwrap` wraps the long-lived `bash -lc` in the same argv profile as one-shot `bash`
(ro system, rw session cwd under `workspace-write`, `--unshare-net` for `net: none`, Landlock
layered when available; a PTY keeps its private controlling terminal, so `--new-session` is
dropped for `tty`), `seatbelt` prefixes `sandbox-exec -p <profile>`, `none` passes it
through (or under `security.sandbox_landlock` wraps it in the Landlock helper). A backend that
cannot wrap a long-lived process (`docker` today, or any backend without the hook that
enforces isolation) makes these tools refuse; they never run unconfined under it.
An explicit backend is probed at startup;
on failure `sandbox_policy: warn` (default) audits `sandbox.fallback` and uses `none`,
`fail` refuses to start. `auoreo doctor` shows the row with `enforced=` and the policy.

`net: proxy` (I9): the child gets `HTTPS_PROXY`/`HTTP_PROXY` pointing at the call's loopback
egress proxy (`src/auoreo/tools/egress.py`). Confinement: `bwrap`/`none`+Landlock restrict TCP
connects to the proxy port with Landlock ABI ≥ 4 (otherwise the env is advisory and doctor's
`egress-sandbox` row says "not enforced"); `seatbelt` allows `localhost:<proxy port>` only;
`docker` gets no network (a container cannot reach the host loopback). Without a running proxy
`proxy` behaves like `none` (network unshared).

Each tool manifest declares `sandbox_mode` (`read-only < workspace-write <
danger-full-access`, default `workspace-write`) and `net` (`none | proxy | direct`).
A wider mode for one call needs `sandbox_mode` + `justification` arguments and an
approval for permission `sandbox_escalate` (never sticky). Host mounts outside the
working directory require `danger-full-access` and are audited as `sandbox.mount`.
