"""`environment` and `host` in the exec-policy request (M7 hosts-b, `m7.ssh.policy_fields`).

A policy can only tell a production host from a lab machine if `req` says where the call
acts. Only the tools that act on an execution environment get values:

- `exec_command`, `env_transfer`: the session's selected environment (`SessionState`);
- `write_stdin`: the environment of the process record it writes to, not the session's
  current one (the agent may have switched since the process started);
- `use_environment`: the environment being requested (`args["name"]`).

`edit`, `read`, `bash` and every other tool act on the local machine whatever the session
selected, so both fields are None for them (else "deny writes on prod" would block local
edits). The values come from session state, the process table and the host files, never
from model-written text. `host` is None unless the environment is SSH; it never carries a
secret name.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from auoreo.core import hosts

ENV_TOOLS = frozenset({"exec_command", "write_stdin", "env_transfer", "use_environment"})


def enabled(settings: Any) -> bool:
    ssh = getattr(getattr(settings, "m7", None), "ssh", None)
    return bool(getattr(ssh, "hosts", False) and getattr(ssh, "policy_fields", False))


def host_fields(svc: Any, name: str) -> dict[str, Any] | None:
    """`{name, address, port, user, origin, tags, jump}` of an SSH environment, else None."""
    env = hosts.resolve_environment(svc, name)
    if env is None or env.backend != "ssh":
        return None
    cfg = env.overrides.get("environment_ssh") or getattr(svc.settings, "environment_ssh", None)
    get: Callable[[str], Any] = cfg.get if isinstance(cfg, dict) else (lambda k: getattr(cfg, k, None))
    ext = env.host
    return {
        "name": name,
        "address": str(get("host") or ""),
        "port": int(get("port") or 22),
        "user": str(get("username") or ""),
        "origin": env.origin,
        "tags": list(ext.tags) if ext is not None else [],
        "jump": ext.jump.name if ext is not None and ext.jump is not None else None,
    }


def _proc_environment(svc: Any, ctx: Any, proc_id: object) -> str | None:
    from auoreo.tools.environments import pool_for

    if not isinstance(proc_id, str):
        return None
    pool = pool_for(svc)
    rec = pool.procs.get(ctx.session_id, {}).get(proc_id)
    if rec is None or pool.instances.get(rec.environment) is not rec.backend:
        return None  # a process on the default backend has no named environment
    return str(rec.environment)


def environment_of(tool: str, ctx: Any, args: dict[str, Any] | None, svc: Any) -> str | None:
    args = args or {}
    if tool == "use_environment":
        name = args.get("name")
        return name if isinstance(name, str) and name not in ("", "default") else None
    if tool == "write_stdin":
        return _proc_environment(svc, ctx, args.get("session_id"))
    if tool in ENV_TOOLS:
        return ctx.state.environment or None
    return None


def env_fields(tool: str, ctx: Any, args: dict[str, Any] | None = None) -> dict[str, Any]:
    """`{environment, host}` for the policy request; `{}` when the flag is off (the request
    then has both keys as None) or the tool acts locally."""
    svc = ctx.services.get("services")
    if svc is None or tool not in ENV_TOOLS or not enabled(svc.settings):
        return {}
    name = environment_of(tool, ctx, args, svc)
    if name is None:
        return {"environment": None, "host": None}
    return {"environment": name, "host": host_fields(svc, name)}


def fields_for(svc: Any, name: str) -> dict[str, Any]:
    """The same fields for a known environment name (the `ssh` gate)."""
    if svc is None or not enabled(svc.settings):
        return {}
    return {"environment": name, "host": host_fields(svc, name)}
