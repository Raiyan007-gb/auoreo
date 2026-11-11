# policies/

`policies/<name>.star` is an **exec policy** written in [Starlark](https://github.com/bazelbuild/starlark)
(evaluated by starlark-rust through `starlark-pyo3`). Policies run on every permission check
**after the floors and before the permission rules** (spec §2.10, §19.4): they can tighten or
loosen what the rules would say, but they can never get past a floor, the bare-`sudo` ask or
the `sudo -n` explicit-allow rule.

## File format

```python
# description: Deny piping downloads into a shell.
# priority: 500          # 0-1000, default 100; higher runs first
# enabled: true          # default true


def decide(req):
    if req["permission"] == "bash" and re_match(r"curl.*\|\s*sh", req["pattern"]):
        return "deny"
    return None
```

The header is `# key: value` comment lines before the first code line (`description` is
required). The file must define `def decide(req):`.

## `req`

| field | meaning |
|---|---|
| `tool` | tool name (`bash`, `edit`, `mcp__srv__x`, …) |
| `permission` | permission name the rules match (`bash`, `edit`, `read`, `sandbox_escalate`, …) |
| `pattern` | the value the rules match: the command for `bash`, the absolute path for file tools, else `*` |
| `mode` | permission mode (`manual`, `smart`, `acceptEdits`, `plan`, `yolo`) |
| `interactive` | `True` when a human can answer an approval |
| `agent`, `session_id`, `cwd` | where the call comes from (may be `None`) |
| `environment` | M7 (`settings.m7.ssh.policy_fields`): the execution environment the call acts on, for `exec_command`, `env_transfer` (the session's selection), `write_stdin` (the environment its process started in) and `use_environment` (the one requested); `None` for every other tool, which acts on the local machine |
| `host` | M7: `{name, address, port, user, origin, tags, jump}` when that environment is an SSH host, else `None`. No secret name. `tags` come from `hosts/<name>.yaml` |

`read_only_host.star` (bundled, `enabled: false`) is an example: copy it to `<home>/policies/`,
set `enabled: true` and tag a host `readonly`. It filters model-written text and is **not**
enforcement: real read-only access is a remote account without write permission.

`req` is a copy: changing it has no effect.

## Return values

| value | effect |
|---|---|
| `None` | no opinion: the next policy, then the permission rules decide |
| `"allow"` | allow (reason `policy:<name>`); bare `sudo` still asks |
| `"deny"` | deny (reason `policy:<name>`) |
| `"ask"` | ask the human (reason `policy:<name>`); `yolo`/`acceptEdits` do not upgrade it |
| `"review"` | ask the **guardian** model (`prompts/guardian.md`, any mode): APPROVE runs it once, DENY refuses, ESCALATE asks the human (refused when non-interactive) |

Enabled policies run highest `priority` first (then by name); the first non-None answer wins.

## Sandbox and failure behaviour

- Starlark has no `while`, no `load()`/imports, no file or network access and no clock;
  policies are pure functions of `req`. Extra helpers: `re_match(pattern, s)` (Python
  `re.search`, returns bool) and `glob_match(pattern, s)` (fnmatch).
- Each `decide` call has a **50 ms** budget. A runtime error, an unknown return value or a
  call over budget answers `review` (`policy_error:<name>`), never `allow`.
- A policy that does not compile is reported by `auoreo harness validate` and `auoreo doctor`
  and **denies every request** (`policy_compile_error:<name>`) until it is fixed (fail closed).

## Examples

```python
# description: Never let agents touch production kubeconfigs.
# priority: 800
def decide(req):
    if req["permission"] == "bash" and "--context=prod" in req["pattern"]:
        return "deny"
    return None
```

```python
# description: Let the reviewer agent run the test suite without asking.
def decide(req):
    if (
        req["agent"] == "reviewer"
        and req["permission"] == "bash"
        and req["pattern"].startswith("uv run pytest")
    ):
        return "allow"
    return None
```

The bundled `default.star` is deliberately narrow: it denies `curl|wget … | sh` and
`git push --force` to `main`/`master`, sends `rm -rf` outside `/tmp/` to review, and has no
opinion on anything else. Disable it with a same-named overlay containing `# enabled: false`.
