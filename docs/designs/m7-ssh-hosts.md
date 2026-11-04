# M7 SSH hosts: named inventory, connect-time gate, host-key trust (wave "hosts-a")

*   **Author:** Raiyan007-gb
*   **Status:** Approved (owner, 2026-11-05)
*   **Date:** 2026-11-05

--------------------------------------------------------------------------------

## Overview

AUOREO can already run commands on remote machines: the `ssh` environment backend
(`backends/environment/ssh/impl.py`, asyncssh) connects with a private key read only from the
secret store, requires a `known_hosts` file (no `known_hosts=None` path), disables the SSH agent
and `~/.ssh/config` (`agent_path=None`, `config=None`), and runs each command as `bash -lc` on its
own channel. Machines are configured as `settings.environments.<name>` entries (`backend: ssh`,
plus an `environment_ssh` override) and selected per session with the `use_environment` tool.
Every remote exec is audited as `environment.exec` with a command hash.

This design adds, as its first wave ("hosts-a"): a named host inventory (`hosts/<name>.yaml`) with
CLI `add|list|test`, trust-on-first-use of the server host key in the CLI only, keepalives, and a
permission gate plus audit row at the point where a session first uses an SSH host. Key generation
and rotation, policy-visible host fields, jump hosts, `ssh import` and usage tags are later waves
(see Future Improvements; owner decision O2).

*Terms.* `<home>` is `$AUOREO_HOME` (default `~/.auoreo/`). The "runtime root" is `<home>`; a
project root `<cwd>/.auoreo/` is inert until the user runs `auoreo trust`. A "folder kind" is a
top-level definition directory such as `tools/` or `policies/` that discovery loads
(`harness/discovery.py`). "Doctor" is `auoreo doctor`. Permission rules are the
`permissions/*.yaml` rules (`permission`, `pattern`, `action`).

## Problem Statement

*   **No inventory.** Hosts are JSON inside `settings.environments`, with no CLI, no test command
    and no recorded state. A host cannot be listed, tested or removed without editing settings.
*   **Host-key trust is manual.** A `known_hosts` file must be provided; a new host fails until
    the user runs `ssh-keyscan` themselves. Nothing shows the server fingerprint.
*   **No keepalive.** Idle connections can be dropped by intermediaries (not measured here).
*   **SSH is outside the network chokepoint, and its permission is weak.** The connection is a raw
    socket from the harness process, so the egress proxy's lists and audit never see it. Today
    `use_environment` calls `backend.check()` (which connects) behind only the generic
    `environment` permission; there is no per-host permission and no network audit row, and the
    model can choose among every configured host. (Both `exec_command` and `use_environment`
    declare `net: none`.)
*   **Key lifecycle is manual** (create elsewhere, paste with `auoreo secrets set`). Deferred to
    wave hosts-b.

## Goals

*   Hosts are definitions: `hosts/<name>.yaml`, discovered by the harness, loaded **from the
    runtime root only**, listed and tested from the CLI.
*   `use_environment <name>` resolves host-file hosts and legacy `settings.environments` entries
    through one function and one backend adapter.
*   Every session's first use of an SSH host (host-file or legacy) passes the `ssh` gate at the
    point the connection is requested, with `net_direct` semantics (D1), and every socket open
    writes a `net.ssh` audit row.
*   Trust-on-first-use happens only in the CLI with an audit row; a changed host key makes the
    runtime backend fail closed, and only `auoreo ssh test --replace-key` accepts the new key.
*   Idle connections are kept alive (`keepalive_interval_s`).

## Non-Goals

*   SSH agent forwarding (the backend deliberately never touches the OS agent).
*   Port forwards, directory sync, tmux reattach, automatic cloud inventory.
*   Trust-on-first-use through the chat approval card: a host key is an operator decision and must
    not be grantable from a messaging app or an unattended policy.
*   Web CRUD for hosts and the `env_transfer` tool.
*   Any database schema migration.
*   Waves hosts-b and hosts-c (key generation/rotation, `req["environment"]`/`req["host"]` for
    Starlark, `env=` usage tags, jump hosts, `ssh import`) are not specified normatively here;
    sketches are in Future Improvements.

## Open Questions

Owner decisions (each has a recommended default that this draft already follows):

*   **O1. Egress posture.** D1 makes the `ssh` gate behave like `net_direct` (non-sticky,
    yolo-proof, justification, grant id), but it still deviates from the AGENTS.md rule that tools
    reach the network only through the egress proxy or a one-shot `net_direct` grant, because a
    persistent session connection does not fit `netstage.net_stage` (which runs per tool call
    for tools whose manifest says `net: direct`). Options: (a) accept the bounded deviation
    (default); (b) amend AGENTS.md to name the "ssh grant" as a second exception; (c) route the
    socket through the proxy now (`CONNECT host:22`, asyncssh `sock=`), which needs a port
    allowance and long-lived proxy tokens.
*   **O2. Scope split.** Default: this design is wave hosts-a only. The manifest is limited to
    `name, host, port, user, key_secret, keepalive_interval_s`; fingerprints and public keys are
    not stored.
*   **O3. Folder kind vs one file.** Default: keep `hosts/<name>.yaml` as a folder kind
    (consistent with "capabilities are folders"), runtime root only. Alternative B (below) is a
    single `<home>/ssh/hosts.yaml` read by `core/hosts.py`, which removes the discovery, loader,
    registry and trust edits (about 5 files).
*   **O4. Unattended runs.** With the flag on and no human, the gate refuses unless an `ssh` allow
    rule exists. Default: fail closed and document the rule migration; do not auto-approve.
*   **O5. Naming.** The earlier draft said "W2" while deferring work to "Wave 2". Default: this
    wave is "hosts-a" until the M7 plan fixes wave numbers.

Technical questions closed before sign-off: the asyncssh API names (`keepalive_interval=`,
host-key retrieval, `known_hosts=`) are checked with Context7 (`resolve-library-id` then
`query-docs`, the repo's documentation tool) as the first task of Phase 1, and the result goes
into the Phase 1 commit as a `Docs:` trailer. If asyncssh offers no key-only probe, the TOFU probe
is a client whose host-key validator records the presented key and aborts before authentication
(D4).

## Critical User Journeys (CUJs)

### CUJ 1: Add and trust a host

*   **Given**: a reachable Linux VM, a private key already stored with
    `auoreo secrets set SSH_KEY_LAB` and its public key in the VM's `authorized_keys`.
*   **When**: the owner runs `auoreo ssh add lab --host 10.0.0.5 --user ubuntu --key SSH_KEY_LAB`,
    then `auoreo ssh test lab`.
*   **Then**: `test` prints the server's SHA-256 host-key fingerprint and asks to trust it
    (`[y/N]`; with no TTY it refuses unless `--fingerprint SHA256:...` matches). On "y" it appends
    the entry to `<home>/ssh/known_hosts`, writes a `host.trust` audit row, and reports a
    successful `echo` round trip. A second `test` connects without asking.

### CUJ 2: An agent works on a named host

*   **Given**: the `lab` host, `settings.m7.ssh.hosts` on, and an interactive session.
*   **When**: the agent calls `use_environment lab` (with a `justification`) and then
    `exec_command "uname -a"`.
*   **Then**: after the generic `environment` permission, the `ssh` gate asks for `lab` (every
    session, never remembered by an `always` reply, not auto-allowed in yolo). On grant a
    `net.ssh_grant` row is written, each socket open writes `net.ssh{host, port, grant_id}`, and
    the command runs remotely with the existing `environment.exec` audit.

### CUJ 3: A changed host key

*   **Given**: `lab` is trusted and the VM is rebuilt with a new host key.
*   **When**: any connection is attempted (agent, `ssh test`, doctor).
*   **Then**: the runtime connection fails closed naming the host and the known_hosts file;
    nothing prompts. `auoreo ssh test lab --replace-key` shows old and new fingerprints, asks,
    replaces the entry under a lock and writes a `host.trust` row with `replaced: true`.

### CUJ 4: A legacy settings entry is gated too

*   **Given**: a `settings.environments.box` SSH entry from before this change and the flag on.
*   **When**: the agent calls `use_environment box`.
*   **Then**: the same `ssh` gate and `net.ssh` audit apply; with the flag off nothing changes.

## Technical Plan (Proposed Architecture)

```text
hosts/<name>.yaml (runtime root only) -discovery/loader/registry-> Harness.hosts {name: HostManifest}
auoreo ssh add|list|test -> core/hosts.py -> <home>/hosts/*.yaml, <home>/ssh/known_hosts

use_environment <name> -> core/hosts.resolve_environment(svc, name)
        settings.environments[name]   (wins on collision; doctor warns)
        else Harness.hosts[name] -> manifest_to_override() -> {"environment_ssh": SshEnvironment(...)}
   -> tools/ssh_gate.gate_ssh(ctx, target) -> grant | refuse
   -> EnvironmentPool.instance(name) -> backend.check()
exec_command / write_stdin -> same gate_ssh (cached per session in SessionState.ssh_granted)
ssh backend _connect -> audit net.ssh{host, port, grant_id} on every socket open
```

### D0. Resolution of `use_environment lab`

*   One function, `core/hosts.resolve_environment(svc, name) -> ResolvedEnv | None`, is the only
    lookup. `ResolvedEnv` has `name`, `backend` ("ssh"), `overrides` (a dict holding
    `environment_ssh`) and `origin` ("settings" or "host-file").
*   `EnvironmentPool.instance`, the `use_environment` handler (`tools/use_environment/handler.py`:
    the `name in envs` test, the unknown-name message and `_listing`) and `environments.describe`
    (doctor) all call it. Nothing else reads `settings.environments` for lookup.
*   Order: `settings.environments[name]` wins; a name in both places makes doctor warn "host file
    `<name>` shadowed by settings.environments". Host-file hosts exist only when
    `settings.m7.ssh.hosts` is on; with the flag off `use_environment lab` reports an unknown
    environment exactly as today.
*   There is no `host` selector key: `settings_for` rejects keys that are not `Settings` fields,
    and `Settings.host` is the bind address (`core/settings.py:68`). A host-file host never becomes
    a `NamedEnvironment`; `resolve_environment` returns `overrides = {"environment_ssh":
    SshEnvironment(...)}` and the pool applies it with the existing `settings_for` mechanics
    (`model_validate({**settings.model_dump(), **overrides})`).
*   One adapter only: manifest to `SshEnvironment` (`host`, `port`, `username=user`, `key_secret`,
    `known_hosts=<home>/ssh/known_hosts`, `keepalive_interval_s`). The backend keeps reading
    `settings.environment_ssh`; it does not take a `HostManifest`.
*   `SshEnvironment` gains one optional field `keepalive_interval_s: float | None = None`. It is
    an `extra="forbid"` model, so a previous build would reject a persisted settings blob that sets
    it. That rollback cost is accepted: the adapter sets the field only in memory for host-file
    hosts (never persisted), and legacy users do not set it.

### D1. The `ssh` gate

*   Placement: `gate_ssh(ctx, target)` in new `tools/ssh_gate.py`, called from `use_environment`
    **before** `backend.check()` and from `exec_command` and `write_stdin` before a remote exec
    whenever `ctx.state.environment` resolves to an SSH backend. It applies to every SSH backend
    instance (host-file and legacy) when the flag is on. Doctor and `auoreo ssh test` are operator
    actions: exempt from the agent gate, and they write `net.ssh` with `actor: "operator"`.
*   Relationship to `environment`: both must allow. `environment` answers "may this session
    switch environments"; `ssh` answers "may it open a socket to this host".
*   Semantics (match `tools/netstage.py` `net_stage`): `ToolContext.authorize` gains
    `sticky: bool = True`; the gate passes `False`, so an `always` reply is not remembered.
    `yolo`, `acceptEdits` and `smart` do not auto-allow (the same `decision.reason == "mode"` test
    as `netstage.py:152`); only an explicit `allow` rule or a human reply in an interactive
    session grants. A human grant requires a non-empty `justification` (new optional argument on
    `use_environment`, validated with the existing `justification_error`); an allow rule needs
    none. The justification is stored with `justifications.store(kind="ssh")`; the grant id is a
    uuid.
*   Cache: `SessionState.ssh_granted: dict[str, str]` (cache key to grant id). It lives for the
    session, is not restored by `restore_state` (a restarted session asks again), and a denial is
    never cached. The key is the host name plus a hash of `host, port, user, key_secret`, so
    editing the definition invalidates the grant.
*   Audit: `AuditService.record(actor="agent", action="net.ssh_grant", ...)` at grant time with
    `detail {mode: "ssh", host, port, grant_id, justificationHash}`; and `action="net.ssh"` with
    `detail {mode: "ssh", host, port, grant_id}` on every socket open in the backend, so silent
    pooled reconnects are recorded. The backend receives an `audit` callable from the pool; with
    none attached (unit tests of the bare backend) it is a no-op.
*   Non-interactive runs: `ctx.authorize` already raises unless a rule allows; this is intended
    (O4). The release note gives the rule (`permission: ssh`, `pattern: <host>`).
*   Pattern: the `ssh` permission pattern is the host name (for example `lab`). Permissions are
    free-form strings evaluated by `PermissionEngine.evaluate`; a test confirms a rule
    `permission: ssh` matches.
*   Address hygiene: `ssh add` and the loader reject link-local and cloud-metadata literals
    (`169.254.0.0/16`, `fe80::/10`, `100.100.100.200`) and `0.0.0.0`. Hostnames are resolved at
    connect time and not re-checked (known limitation, see Risks).
*   Net label: the two tools keep `net: none`, because `net_stage` runs per call and would demand
    a justification even for local environments. This is stated in O1 and Risks; the `net.ssh*`
    rows are the compensating audit.

### D2. Host keys in the per-call secret scope

*   `tools/gates.secret_names(tool, settings, harness)` gains `host_key_names(harness, tool)`: for
    `use_environment`, `exec_command`, `write_stdin` only, it adds the `key_secret` names of
    `Harness.hosts` entries. Only runtime-root hosts exist (D3), so a project file cannot widen
    the scope. Other tools never receive host key names.
*   The backend reads the key with `SecretStore.candidates(name)` (current value, then the `#prev`
    value inside its grace window; `core/secrets.py:235`) and passes the list to `client_keys`.
    The read is audited by the store with `by="backend:ssh"`; if `candidates` does not accept
    `by`, Phase 3 adds it.
*   Legacy entries already work because `_operator_refs` collects `secret:` refs from settings.
*   `auoreo ssh test` runs in the CLI process, outside any tool scope, and reads the key through
    the same store method.

### D3. Roots, trust and files that can change hosts

*   `hosts` is added to `UNTRUSTED_SKIP` (`harness/trust.py:27`), so a hostile project
    `hosts/*.yaml` lands in `snap.untrusted` and is never active. Even in a trusted project or an
    `extra_roots` root, `load_host` accepts only candidates from the runtime root and records a
    note otherwise, because a host file chooses which stored key is offered to which address.
*   `core/permissions.SENSITIVE_WRITE_GLOBS` gains `$AUOREO_HOME/hosts/*` and
    `$AUOREO_HOME/ssh/*`, so an auto-allowed edit cannot create a host or append a trusted key. A
    local shell command can still write them, as it can today.

### D4. Trust-on-first-use

*   `core/hosts.probe_host_key(host, port, timeout) -> ProbeResult(key_type, fingerprint_sha256,
    openssh_line)` opens a connection with a validator that records the presented key and aborts
    before authentication. It exists only on the CLI path (`ssh test`); the runtime backend never
    gains a bypass and keeps `_problems()` requiring an existing `known_hosts`.
*   On a first `test`, `known_hosts` may not exist: the CLI creates an empty file before probing.
*   `trust(home, host, port, line, *, replace=False)` writes the `[host]:port key-type base64` form
    (plain `host` for port 22), unhashed, idempotent when the same key is present, and replaces the
    matching entry when `replace=True`. It rewrites atomically under the cross-platform file lock.
*   `--fingerprint` pins a non-interactive trust. At runtime a changed key raises the existing
    fail-closed message; only `test` prints expected and presented fingerprints, because only the
    probe sees the presented key.
*   `<home>/ssh/known_hosts` is separate from any `known_hosts` a legacy entry names; legacy
    paths keep working and are never rewritten by the CLI.

### Helpers reused

*   Cross-platform file lock: the context manager in `core/db_migrate.py:48-71` (msvcrt on Windows,
    flock elsewhere), lifted to `core/filelock.py` if importing `db_migrate` from `core/hosts.py`
    is a layering problem. The `fcntl`-only locks (`automation/lock.py`, `core/secrets.py`) are not
    used.
*   Private atomic write: the `write_private` function at `automation/cron_store.py:140`. `core`
    must not import `automation`, so Phase 2 moves it to `core/fsutil.py` and re-exports it from
    `cron_store`. The 0600 mode is a no-op on Windows; the docs say `known_hosts` relies on the
    user-profile ACL there instead of claiming owner-only. It holds public data only.
*   Reserved-name check: a `RESERVED_NAMES` validator beside `NAME_RE` in `harness/manifests.py`,
    case-insensitive. `NAME_RE` is `^[a-z][a-z0-9_]*$`, so host names contain no `.`, `-` or path
    separators (no traversal).

### Manifest (wave hosts-a)

`HostManifest` (`extra="forbid"`): `name` (`NAME_RE` plus the reserved check), `host`, `port`
(default 22), `user`, `key_secret` (`secret:<NAME>`, the pattern of `SshEnvironment.key_secret`),
`keepalive_interval_s` (default 30, 0 disables). `--key` on the CLI takes the secret **name**
(`SSH_KEY_LAB`) and the manifest stores `secret:SSH_KEY_LAB`; name derivation arrives with
`keygen`. Key material, public keys and fingerprints are not stored.

## Alternatives Considered

### Alternative A: Keep hosts in `settings.environments`

*   **Pros**: no new folder kind; already supported by `use_environment` and doctor; no discovery,
    registry or trust edits.
*   **Cons**: every edit bumps the settings version and fires the live-apply watchers (the
    in-process refresh of changed settings); no per-host review or shadowing; a growing JSON blob
    with no CLI. The forbid-model rollback cost applies equally to `keepalive_interval_s` and is
    accepted in D0.
*   **Reason for Rejection**: for a handful of hosts this is a close call. Kept as a folder kind
    only because host files can be added, removed and reviewed one file at a time; ratified by O3.

### Alternative B: One file `<home>/ssh/hosts.yaml` read by `core/hosts.py`

*   **Pros**: no discovery, loader, registry, trust or `Snapshot` changes; the untrusted-root
    problem disappears.
*   **Cons**: hand-written validation, no overlay/reload semantics, a second inventory format
    beside folders.
*   **Reason for Rejection**: pending O3 (this is the main alternative to ratify or pick).

### Alternative C: Route the SSH socket through the egress proxy now

*   **Pros**: one chokepoint for allow/deny lists, SSRF rules and network audit.
*   **Cons**: needs a port-22 allowance and a long-lived proxy token per connection; a proxy
    restart would drop every remote session; its allowlist semantics need their own review.
*   **Reason for Rejection**: not adopted now; it is O1 option (c).

### Alternative D: Trust-on-first-use through the approval card

*   **Pros**: works from the web UI mid-session.
*   **Cons**: approvals can be answered from chat apps or auto-approved by unattended policies,
    and an `always` reply would mean "trust any key from this host".
*   **Reason for Rejection**: a host key is an operator decision; it stays a CLI action.

--------------------------------------------------------------------------------

## Non-Functional Requirements

*   **Security & Privacy**: private keys only in the secret store (never in a host file, audit row
    or log); `known_hosts` in a harness-owned file; audit rows keep the command hash only
    (`redacted` text is deferred); hosts load from the runtime root only; the `ssh` gate is
    non-sticky and yolo-proof.
*   **Rollout & Rollback**: `settings.m7.ssh.hosts` (`core/settings_m7.py`, catalog entry in
    `core/env_catalog_data.py`) gates host-file resolution, the `ssh` gate and the audit rows for
    all SSH environments. Flag off restores today's behaviour for the agent. The CLI
    (`ssh add|list|test`) works with the flag off (it only manages files). Builds without the
    `hosts` kind ignore host files. `SshEnvironment.keepalive_interval_s` is the only settings-model
    change (D0); no database migration.
*   **Scalability & Failure behaviour**: designed for tens of hosts; `list` reads manifests only.
    Doctor checks environments serially with a 10 s limit each; with many hosts it runs them
    with `asyncio.gather` capped at 4. A missing key secret, an unreachable host and a changed key
    each give a one-line error naming the host (and the secret name, never its value); the backend
    closes its connection in `finally`.
*   **Portability**: the in-process asyncssh test server spawns `bash`, so on Windows the tests
    need Git Bash on PATH. The suite skips with `shutil.which("bash")` (added in this change;
    today `tests/unit/test_env_ssh.py` skips only when asyncssh is missing). Lock and private-write
    behaviour on Windows are in "Helpers reused".

## Risks & Mitigations

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Deviation from the proxy-only egress rule (O1) | The agent can request raw sockets to any host a definition names | `net_direct` semantics (non-sticky, justification, grant id, yolo-proof); runtime-root hosts only; address checks at `add`; `net.ssh*` audit; owner picks O1 |
| Hostile project `hosts/*.yaml` | Key offered to an attacker host | `UNTRUSTED_SKIP` entry, runtime-root-only loader, test that it lands in `snap.untrusted` |
| Agent edits `hosts/` or `known_hosts` | Plants a host or a trusted key | Sensitive-write globs; a local shell command remains a pre-existing gap |
| Changed host key | Possible MITM | Runtime fails closed with no prompt; `--replace-key` only via CLI, audited |
| Hostname later resolves to a private or metadata address | SSRF-like reach | Known limitation (literals checked at `add` only); O1(c) closes it |
| Flag on, unattended jobs use SSH | Jobs fail closed | O4; release note with the allow rule |
| Stale grant after a host edit | Old grant covers a new target | Cache key hashes `host, port, user, key_secret` |
| Concurrent `trust()` writers | Corrupt known_hosts | Cross-platform lock plus atomic replace; two-writer test |
| Missing key secret or unreachable host | Opaque error | Message names the secret name, not its value; no half-open connection |
| Host-file name shadowed by settings | Wrong host used | Settings wins deterministically; doctor warns |
| Windows file permissions | Files readable by other users | Only public data stored; docs state the ACL reliance |
| Pooled connection shared across sessions | One session's grant reused by another | The grant is per session in `SessionState`; the pool shares only the socket; each use passes the gate first |

## Additional Resources

*   `docs/environments.md`, `docs/remote.md`, the M7 plan (the earlier "W2-1 to W2-8" findings live
    in that plan; this design cites the consolidated review instead)
*   [AgentsRoom SSH connections](https://agentsroom.dev/features/ssh-connections) (keeps credentials
    local and saves named profiles; the earlier Orca and sshc claims were removed as unsourced)

## Future Improvements

*   **hosts-b**: `auoreo ssh keygen` (ed25519 into the secret store; `public_key` and the client-key
    fingerprint written to the host file), manual rotation steps using the `#prev` grace value,
    `req["environment"]` and `req["host"]` for Starlark (needs a `core/policy.build_req`
    extraction to keep `core/permissions.py` at 500 lines, since `PermissionEngine.policy` builds
    `req` by hand at `permissions.py:416`, plus `REQ_FIELDS` and `policy_context`), and a
    `deny-writes-on-host.star` example documented as a soft command denylist (`write_stdin` can
    bypass it), not read-only enforcement.
*   **hosts-c**: one-hop jump hosts (cycle check; same gate, audit and trust as the target),
    `ssh import` (Host to name, HostName to host, User to user, Port to port, ProxyJump to jump),
    `env=<name>` usage tags with a named producer in `runtime/runner.py`.
*   Egress-proxy routing for SSH; `env_transfer`; web host CRUD; `audit_commands: redacted`.

--------------------------------------------------------------------------------

## Detailed Implementation Plan

### Phasing

Each phase leaves the tree green (ruff, mypy, pytest) and sits behind the flag.

1.  **Manifest and harness plumbing**: asyncssh Context7 check, `manifests_hosts.py`,
    `loader_ops.py` extraction, discovery/registry/trust edits, `harness validate`.
2.  **Hosts core**: `core/hosts.py`, `core/fsutil.py`, lock, sensitive globs, settings flag.
3.  **Backend and resolution**: `SshEnvironment.keepalive_interval_s`, ssh backend changes,
    `resolve_environment`, pool/handler/doctor, secret scope.
4.  **Gate and audit**: `tools/ssh_gate.py`, `SessionState.ssh_granted`, `authorize(sticky=)`,
    audit rows.
5.  **CLI and docs**: `cli/commands/ssh.py`, registration, docs.

### Target Files to Create/Modify

#### 1. `src/auoreo/harness/manifests_hosts.py` (Create); `src/auoreo/harness/manifests.py` (Modify)

*   `HostManifest` as above on the strict base model; `manifests.py` gains `RESERVED_NAMES` (`con`,
    `prn`, `aux`, `nul`, `com1`-`com9`, `lpt1`-`lpt9`, compared case-insensitively) and a reusable
    validator.

#### 2. `src/auoreo/harness/loader_ops.py` (Create); `src/auoreo/harness/loader.py` (Modify)

*   `loader.py` is exactly 500 lines. Move `load_hook`, `load_job`, `load_webhook`, `load_plugin`
    and `load_policy` (lines 400-465, about 66 lines) into `loader_ops.py` together with the new
    `load_host`. `loader.py` re-imports them just above `LOADERS` and adds the `"hosts": load_host`
    entry; the net result is about 440 lines. `loader_ops` imports the private helpers
    (`_validate`, `_parse_yaml`, `_check_name`, `LoadError`, `Loaded`) from `loader`; a test imports
    both modules in either order to catch a cycle. `load_host` enforces runtime-root only (D3).

#### 3. `src/auoreo/harness/discovery.py`, `harness/registry.py`, `harness/trust.py` (Modify)

*   `discovery.LAYOUT["hosts"] = ("file", "*.yaml")`.
*   `registry.NAMED_KINDS` gains `"hosts"` (drives the snapshot load at line 133 and the reload
    diff at line 288); `Snapshot.hosts` and a `Harness.hosts` property. `registry.py` is 477 lines
    and must stay under 500.
*   `trust.UNTRUSTED_SKIP` gains `"hosts"`. Test: a project `hosts/x.yaml` in an untrusted root
    appears in `snap.untrusted` and not in `Harness.hosts`.

#### 4. `src/auoreo/core/hosts.py`, `src/auoreo/core/fsutil.py` (Create); `src/auoreo/automation/cron_store.py` (Modify)

*   `hosts.py` (typed): `ssh_dir(home: Path) -> Path`; `write_host(home: Path, name: str, fields:
    Mapping[str, Any], *, force: bool = False) -> Path` (validates through `HostManifest`, writes
    YAML atomically, refuses an existing file unless `force`); `manifest_to_override(m:
    HostManifest, home: Path) -> dict[str, Any]`; `resolve_environment(svc, name) ->
    ResolvedEnv | None`; `environment_names(svc) -> list[str]`; `probe_host_key(...)`; `trust(...)`
    (D4); `reject_unsafe_address(host: str) -> None`.
*   `fsutil.py`: `write_private` moved from `cron_store.py`, which re-exports it.

#### 5. `src/auoreo/core/settings_models.py`, `core/settings_m7.py`, `core/env_catalog_data.py` (Modify)

*   `SshEnvironment.keepalive_interval_s`; the `M7Ssh.hosts` flag (`settings_m7.py` is currently
    untracked and must be committed with this change); catalog entry so Settings -> Environment
    shows it. The flag is read per call, so it needs no `live_handlers` refresh and no
    `RESTART_REQUIRED` entry (a test asserts this).

#### 6. `src/auoreo/backends/environment/ssh/impl.py` (Modify)

*   `_connect`: pass `keepalive_interval`; read keys with `SecretStore.candidates` and try them in
    order, moving to the next only on key-import or authentication failure; call the pool's audit
    callable on every open. `agent_path=None`, `config=None` and the required `known_hosts` stay.

#### 7. `src/auoreo/tools/environments.py`, `tools/use_environment/handler.py`, `tools/use_environment/tool.yaml` (Modify)

*   `EnvironmentPool.instance`, the handler and `describe` call `resolve_environment` (D0); the
    handler calls `gate_ssh` before `check()`; `tool.yaml` gains the optional `justification`
    argument.

#### 8. `src/auoreo/tools/ssh_gate.py` (Create); `tools/context.py`, `tools/gates.py` (Modify); `tools/exec_command/handler.py`, `tools/write_stdin/handler.py` (Modify)

*   `gate_ssh(ctx, target) -> str | None` implementing D1; `SessionState.ssh_granted` (beside
    `environment`, `context.py:98`; excluded from `restore_state`); `ToolContext.authorize(...,
    sticky: bool = True, context: Mapping[str, Any] | None = None)` (the new `context` parameter is
    unused in this wave and is the seam hosts-b needs); `gates.secret_names` host-key scope (D2);
    `exec_command` and `write_stdin` call the gate when the selected environment is SSH.

#### 9. `src/auoreo/core/permissions.py` (Modify, net zero lines)

*   Add the two sensitive-write globs while keeping the file at 500 lines; if that is impossible,
    extract the glob tuple to `core/permissions_globs.py`.

#### 10. `src/auoreo/cli/commands/ssh.py` (Create); `src/auoreo/cli/app.py` (Modify)

*   Registered with `add_typer` like `cli/commands/secrets.py` (`app.py:19-23`, `171-175`).

    | Command | Flags | Output / exit |
    | --- | --- | --- |
    | `ssh add NAME` | `--host`, `--user`, `--port 22`, `--key SECRET_NAME`, `--keepalive 30`, `--force` | prints the file path; exit 2 on invalid name, unsafe address or existing file |
    | `ssh list` | `--json` | name, host:port, user, key secret name, trusted yes/no |
    | `ssh test NAME` | `--replace-key`, `--fingerprint SHA256:...` | fingerprint as `SHA256:<base64>`; prompt `[y/N]`; exit 0 ok, 1 connect failure, 3 key mismatch or refused |

#### 11. `docs/environments.md`, `docs/remote.md` (Modify)

*   `environments.md`: host files, the flag, the gate, TOFU, the shadowing rule, legacy
    `known_hosts` coexistence, the Windows ACL note. `remote.md`: no change to the claim that a
    dropped connection kills its processes; this design no longer says otherwise.

#### 12. Tests

*   Create `tests/unit/test_ssh_hosts.py`: manifest validation (forbid, reserved names in mixed
    case, no traversal), runtime-root-only and untrusted-root inertness, `write_host` refuses
    overwrite, `trust` format/idempotency/replace/two-writer lock, address rejection, import order
    of `loader` and `loader_ops`, the new `SENSITIVE_WRITE_GLOBS` entries.
*   Modify `tests/unit/test_env_ssh.py` (add the `shutil.which("bash")` skip): host-file
    resolution via `use_environment` and doctor; settings shadowing and doctor warning; flag off
    equals today; gate asks, `always` is not remembered, yolo still asks, an allow rule grants,
    non-interactive refuses, a legacy entry is gated; `net.ssh_grant` and `net.ssh` row content
    including a forced reconnect; key scope (host key readable only in the three tools); `#prev`
    candidate connects; changed key fails closed without prompting; `ssh test --replace-key`
    writes `host.trust`; keepalive is passed.
*   Modify `tests/unit/test_use_environment.py`: the unknown-name message lists both sources.

### Verification Plan

*   **Static analysis & linting**: `uv run ruff check .`, `uv run ruff format --check .`,
    `uv run mypy src`; record the baseline error count and commit before Phase 1.
*   **Tests**: `uv run pytest` against the recorded baseline; `./scripts/run_tests.sh` (hermetic).
*   **Harness**: `uv run auoreo harness validate`; `uv run python scripts/check_openapi.py --check`
    is expected unchanged (no API change).
*   **Manual**: covered by the unit tests using the in-process asyncssh fixture; there is no
    launcher for it, so no manual procedure is claimed.

--------------------------------------------------------------------------------

*Drafted with EGM*
