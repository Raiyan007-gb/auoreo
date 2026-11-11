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

## SSH hosts (`hosts/<name>.yaml`, flag `m7.ssh.hosts`)

A host can also be a file, managed from the CLI instead of the settings JSON:

```bash
auoreo secrets set SSH_KEY_LAB < key           # the private key; its public half goes on the host
auoreo ssh add lab --host 10.0.0.5 --user ubuntu --key SSH_KEY_LAB   # writes <home>/hosts/lab.yaml
auoreo ssh test lab                            # shows the host-key fingerprint, asks, then runs `echo`
auoreo ssh list                                # name, address, user, key secret name, trusted yes/no
auoreo config set m7.ssh.hosts true            # lets agents use host files (off by default)
```

```yaml
# <home>/hosts/lab.yaml: no key material, ever
name: lab
host: 10.0.0.5
port: 22
user: ubuntu
key_secret: secret:SSH_KEY_LAB     # the NAME of a stored secret
keepalive_interval_s: 30           # 0 disables keepalives
```

- **Runtime root only.** Host files load from `<home>/hosts/` and nowhere else: a project
  `.auoreo/hosts/` (trusted or not), an `extra_roots` folder and the bundled definitions are
  ignored, because a host file chooses which stored key is offered to which address. Edits to
  `<home>/hosts/*` and `<home>/ssh/*` by the edit tools always ask, also under `yolo`.
- **Names.** `settings.environments.<name>` wins over a host file of the same name; `auoreo
  doctor` then warns "host file `<name>` shadowed by settings.environments". Windows device
  names (`con`, `nul`, `com1`, …) are refused. `--host` may not be a link-local or metadata
  address literal (`169.254.0.0/16`, `fe80::/10`, `100.100.100.200`, `fd00:ec2::254`) or
  `0.0.0.0`, also spelled as an IPv6 form that wraps the IPv4 address (mapped, 6to4 `2002::/16`,
  NAT64 `64:ff9b::/96`, `::a.b.c.d`); a hostname is resolved at connect time and not re-checked.
- **Flag off** (the default): agents see no host files (`use_environment lab` is an unknown
  environment) and nothing below applies. The `auoreo ssh` commands work either way: they only
  manage files.

### Host keys: trust on first use, in the CLI only

`auoreo ssh test <name>` is the only way a server host key becomes trusted. It prints the
SHA-256 fingerprint and asks `[y/N]`; without a terminal it refuses unless
`--fingerprint SHA256:…` matches. Accepted keys go to `<home>/ssh/known_hosts` (unhashed
OpenSSH lines) and each decision is audited as `host.trust{name, host, port, key_type,
fingerprint, replaced}`.

A key that differs from `known_hosts` is an **error, never a prompt**: every runtime connection
fails closed, and `auoreo ssh test` exits 3 showing the expected and presented fingerprints.
Only `auoreo ssh test <name> --replace-key` (again confirmed, or pinned with `--fingerprint`)
replaces the entry (`host.trust{replaced: true}`). Approval cards, chat apps and unattended
policies cannot trust a host key. Exit codes: 0 ok, 1 cannot connect, 2 unknown host or
invalid input, 3 key not trusted or changed.

`<home>/ssh/known_hosts` is separate from the `known_hosts` a `settings.environments` entry
names; those files keep working and are never rewritten by the CLI. The file holds public
data only. It is written 0600; on Windows the mode is a no-op and the file relies on the
user-profile ACL.

### The `ssh` permission (per host, every session)

With the flag on, a session's first use of **any** SSH environment (a host file, a
`settings.environments` entry with `backend: ssh`, or the default backend when it is `ssh`)
must pass permission `ssh` with the environment name as the pattern (`default` for the default
backend), in addition to the `environment` permission of `use_environment`:

- only an explicit `allow` rule or a human reply grants it; `yolo`, `acceptEdits` and `smart`
  do not, and an "always" reply is not remembered (the next session asks again);
- a human grant needs a `justification` argument on `use_environment` (kept 24 h in
  `<home>/egress/justifications.json`, hash in the audit row); a rule grant needs none;
- the grant is per session and per target: editing the host file asks again and closes the
  pooled connection; a rejection is not cached;
- non-interactive runs (jobs, webhooks, `auoreo run` without a terminal) fail closed unless a
  rule allows the host:

```yaml
# <home>/permissions/ssh.yaml
rules:
  - {permission: ssh, pattern: lab, action: allow}
```

Turning the flag on therefore makes existing unattended jobs that use an SSH environment fail
until such a rule exists.

**Egress deviation.** SSH is a raw socket from the harness process: it does not pass the egress
proxy, and `use_environment`/`exec_command`/`write_stdin` stay `net: none` (the per-call net
stage would demand a justification for local environments too). This is a bounded exception to
"tools reach the network only through the egress proxy or a one-shot `net_direct` grant". The
compensating controls are the `ssh` gate above, runtime-root-only host files, the address
checks at `ssh add`, and two audit rows: `net.ssh_grant{mode, name, host, port, grant_id,
justificationHash}` at grant time and `net.ssh{mode, name, host, port, grant_id}` on every
socket open, pooled reconnects included (`actor: operator` for doctor and `auoreo ssh test`).
Routing the socket through the proxy (`CONNECT host:22`) is a possible follow-up.

Also with the flag on: the key is tried as the current secret value, then `<NAME>#prev` inside
its rotation grace window (`auoreo secrets rotate`), and host key names are in the secret scope
of the three environment tools only.

Not in this wave: `ssh keygen`, `ssh import`, jump hosts, key rotation helpers, `environment`
and `host` fields in Starlark policy requests, `env=` usage tags, web host management.

## Audit and doctor

Every exec (and every non-empty `write_stdin`) on a remote backend is audited as
`environment.exec{backend, environment, remote: true, cmd_hash}` (sha256 prefix, no command
text). `auoreo doctor` shows the selected `backend:environment` row plus one
`environment:<name>` row per named environment with its `check()` (host files included when
`m7.ssh.hosts` is on).
