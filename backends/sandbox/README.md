# backends/sandbox/

Process confinement for tools that run model-supplied commands (`bash` today). ABC:
`src/auoreo/backends/sandbox.py` — `run(ExecSpec) -> ExecResult`, `capabilities()`,
`check()`.

| backend | default | enforces | needs |
|---|---|---|---|
| `none` | yes | nothing (`enforced=False`; the model is told once per session) | — |
| `docker` | no | fs + net (cap_drop ALL, no-new-privileges, uid 1000, pids/mem limits, `network_mode=none` for `net: none`) | `uv sync --extra sandbox-docker` + a daemon |
| `bwrap` | no | fs + net (ro `/usr`, private `/proc` `/dev` PID ns, `--unshare-net` for `net: none`) | `bwrap` on PATH; Landlock layered inside when the kernel has it (ABI in `check()`) |
| `seatbelt` | no | fs + net (`/usr/bin/sandbox-exec -p <SBPL>`: deny default, ro system, rw cwd + tmp, loopback-only for `net: proxy`) | macOS |

`none` + `security.sandbox_landlock: true` runs commands under the Landlock helper (fs only).

Select with `settings.backends.sandbox`. Unset (or `"auto"`) probes the strongest local
backend: `bwrap` on Linux, `seatbelt` on macOS; only when none is usable does it run `none`
(warning + `sandbox.fallback` audit row + doctor warn; `sandbox_policy: fail` refuses).
Under an enforcing backend the persistent shells (`exec_command`, `terminal_pty`) refuse:
set `backends.sandbox: "none"` explicitly to use them unconfined.
An explicit backend is probed at startup;
on failure `sandbox_policy: warn` (default) audits `sandbox.fallback` and uses `none`,
`fail` refuses to start. `auoreo doctor` shows the row with `enforced=` and the policy.

Each tool manifest declares `sandbox_mode` (`read-only < workspace-write <
danger-full-access`, default `workspace-write`) and `net` (`none | proxy | direct`).
A wider mode for one call needs `sandbox_mode` + `justification` arguments and an
approval for permission `sandbox_escalate` (never sticky). Host mounts outside the
working directory require `danger-full-access` and are audited as `sandbox.mount`.
