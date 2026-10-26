"""Custom variables (`settings.env.custom`): naming rules, shell injection, the agent's view and
`{{var.NAME}}` placeholders.

- `shell_env(svc, tool)` — the variables flagged `expose_to_shell`, for the shell tools only
  (`SHELL_TOOLS` + `auoreo exec`), added on top of the scrubbed child env. A secret value is
  resolved from secrets.db, audited `secret.read{name, by}` and registered with the redactor
  (`refresh_redaction`) so it is masked in tool output and events.
- `agent_block(settings)` — a short section of the per-turn context block (never the cache-stable
  system prompt) listing the names flagged `expose_to_agent`; plain values are shown, secret
  values never are.
- `resolve_vars(text, settings)` — `{{var.NAME}}` → a plain variable's value in command
  templates, job prompts and workflow step inputs; secret or unknown names render empty.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from auoreo.core.dotenv import _REDIRECT, PROTECTED_PREFIXES
from auoreo.core.settings_env import CUSTOM_NAME

log = logging.getLogger(__name__)
SHELL_TOOLS = frozenset({"bash", "exec_command", "terminal_pty", "monitor", "run_code", "pwsh"})
VAR_PLACEHOLDER = re.compile(r"\{\{\s*var\.([A-Z][A-Z0-9_]{1,63})\s*\}\}")
_RESERVED_EXTRA = re.compile(r"^(AUOREO_|LD_|DYLD_|PYTHON|NODE_|GIT_|SSL_CERT|LITELLM_)|_PROXY$")
_LITERAL_SET = "env.custom"


def name_problem(name: str) -> str | None:
    """Why `name` cannot be a custom variable, or None when it can."""
    from auoreo.core.env_catalog import by_name

    if not CUSTOM_NAME.match(name):
        return "use A-Z, 0-9 and _ (2-64 characters, starting with a letter)"
    if any(name.startswith(p) for p in PROTECTED_PREFIXES) or _RESERVED_EXTRA.search(name):
        return "AUOREO_* and loader/redirect names are reserved"
    if _REDIRECT.search(name):
        return "names that redirect traffic or code loading (PATH, HOME, *_URL, *_HOST, …) are reserved"
    if name in by_name():
        return "this is a built-in variable: edit it in its section above"
    return None


def custom(settings: Any) -> dict[str, Any]:
    env = getattr(settings, "env", None)
    return dict(env.custom) if env is not None else {}


def plain_values(settings: Any) -> dict[str, str]:
    return {n: v.value or "" for n, v in custom(settings).items() if not v.secret}


def secret_value(svc: Any, name: str) -> str | None:
    try:
        return svc.secrets._resolve(name)  # type: ignore[no-any-return]
    except Exception:  # the store is unavailable: the variable is simply not injected
        log.warning("custom secret %s could not be read", name)
        return None


def shell_env(svc: Any, tool: str) -> dict[str, str]:
    """Custom variables flagged `expose_to_shell`, for shell tools only."""
    if tool not in SHELL_TOOLS and not tool.startswith("cli:"):
        return {}
    out: dict[str, str] = {}
    for name, var in custom(svc.settings).items():
        if not var.expose_to_shell:
            continue
        if not var.secret:
            out[name] = var.value or ""
            continue
        value = secret_value(svc, name)
        if value is None:
            continue
        svc.secrets._audit(name, tool, True)  # `secret.read{name, by}`, never the value
        out[name] = value
    return out


def refresh_redaction(svc: Any) -> None:
    """Register the current secret custom values as literal redactions (output masking)."""
    from auoreo.core.redaction import set_literals

    values = {}
    for name, var in custom(svc.settings).items():
        if var.secret and (value := secret_value(svc, name)):
            values[name] = value
    set_literals(_LITERAL_SET, values)


def agent_block(settings: Any) -> str:
    """The context-block lines for `expose_to_agent` variables ('' when there are none)."""
    rows: list[str] = []
    for name, var in sorted(custom(settings).items()):
        if not var.expose_to_agent:
            continue
        where = "available as $" + name if var.expose_to_shell else "not exported to shell commands"
        if var.secret:
            rows.append(f"- {name} (secret; value hidden; {where})")
        else:
            rows.append(f"- {name} = {(var.value or '')[:200]} ({where})")
    if not rows:
        return ""
    return "Custom environment variables the user configured:\n" + "\n".join(rows)


def resolve_vars(text: str, settings: Any) -> str:
    """`{{var.NAME}}` → plain value; secret/unknown names become '' (never a secret value)."""
    if "var." not in text:
        return text
    plain = plain_values(settings)
    return VAR_PLACEHOLDER.sub(lambda m: plain.get(m.group(1), ""), text)


__all__ = [
    "SHELL_TOOLS",
    "agent_block",
    "custom",
    "name_problem",
    "plain_values",
    "refresh_redaction",
    "resolve_vars",
    "shell_env",
]
