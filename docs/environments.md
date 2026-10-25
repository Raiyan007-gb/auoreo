# Execution environments

`exec_command` / `write_stdin` run in an **environment backend** (`backends/environment/<name>/`,
ABC `src/auoreo/backends/environment.py`). Permission rules, floors and exec policies are
evaluated by the executor before any backend runs; `write_stdin` also applies the bash floors
to every line it sends, wherever the process lives.

| Backend | Where | Remote | Status |
|---|---|---|---|
| `local` (default) | this machine, persistent `bash -lc` (PTY or pipes) | no | tested for real; refuses under an enforcing sandbox |
| `docker` | one container per session, cwd at `/work`, `cap_drop ALL`, no-new-privileges, uid 1000, network `environment_docker_network` (default `none`) | no | fake Docker client in tests (needs extra `sandbox-docker` + a daemon) |
| `ssh` | a host via asyncssh (extra `ssh`) | yes | tested for real against an in-process asyncssh server |
| `e2b` | E2B sandbox per session (`e2b` SDK; `check()` via REST) | yes | contract-tested only |
| `modal` | Modal Sandbox per session (`modal` SDK) | yes | contract-tested only |
| `daytona` | Daytona sandbox per session (REST, httpx); no stdin | yes | contract-tested only |

Cloud sandboxes and docker containers are created on the first command of a session and
destroyed when the session is archived or deleted (and at server shutdown).

## Selecting

- Default for every session: `settings.backends.environment` (else `local`).
- Per session: the deferred tool `use_environment {name}` (permission `environment`, default
  `ask`) picks an entry of `settings.environments`; `"default"` resets, `""` lists. The switch is
  refused when the backend's `check()` fails. Processes keep running where they started.

```json
{
  "environments": {
    "lab": {
      "backend": "ssh",
      "description": "GPU box",
      "environment_ssh": {
        "host": "lab.internal", "username": "agent",
        "key_secret": "secret:LAB_SSH_KEY", "known_hosts": "~/.auoreo/known_hosts"
      }
    },
    "box": {"backend": "docker", "environment_docker_network": "bridge"}
  }
}
```

Every key besides `backend`/`description` overrides a `Settings` field for that environment's
backend instance (unknown keys are refused).

## SSH rules

- The private key comes only from the secret store (`auoreo secrets set LAB_SSH_KEY < key`),
  referenced as `secret:<NAME>`; never `~/.ssh`, an agent, or a file path.
- `known_hosts` is required; a host-key mismatch or a connect timeout fails closed.
- The local child environment is never shipped to a remote host.

## Audit and doctor

Every exec (and every non-empty `write_stdin`) on a remote backend is audited as
`environment.exec{backend, environment, remote: true, cmd_hash}` (sha256 prefix, no command
text). `auoreo doctor` shows the selected `backend:environment` row plus one
`environment:<name>` row per named environment with its `check()`.
