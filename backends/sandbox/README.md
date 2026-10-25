# backends/sandbox/

Process confinement for tools that run model-supplied commands (`bash` today). ABC:
`src/auoreo/backends/sandbox.py` — `run(ExecSpec) -> ExecResult`, `capabilities()`,
`check()`.

| backend | default | enforces | needs |
|---|---|---|---|
| `none` | yes | nothing (`enforced=False`; the model is told once per session) | — |
| `docker` | no | fs + net (cap_drop ALL, no-new-privileges, uid 1000, pids/mem limits, `network_mode=none` for `net: none`) | `uv sync --extra sandbox-docker` + a daemon |
| `bwrap` | no | fs + net (ro `/usr`, private `/proc` `/dev` PID ns, `--unshare-net` for `net: none`) | `bwrap` on PATH; Landlock not layered yet |

Select with `settings.backends.sandbox`. At startup the selected backend is probed;
on failure `sandbox_policy: warn` (default) audits `sandbox.fallback` and uses `none`,
`fail` refuses to start. `auoreo doctor` shows the row with `enforced=` and the policy.

Each tool manifest declares `sandbox_mode` (`read-only < workspace-write <
danger-full-access`, default `workspace-write`) and `net` (`none | proxy | direct`).
A wider mode for one call needs `sandbox_mode` + `justification` arguments and an
approval for permission `sandbox_escalate` (never sticky). Host mounts outside the
working directory require `danger-full-access` and are audited as `sandbox.mount`.
