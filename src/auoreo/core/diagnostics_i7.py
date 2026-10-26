"""I7 doctor rows: OAuth connectors (configured? signed in?) and the Discord privileged-intent
gate (spec §25.15 "privileged-intent doctor gate").

The Discord row only runs when the discord plugin is enabled with its Gateway connector and a
bot token is stored: `GET /applications/@me` → `flags` must carry GATEWAY_MESSAGE_CONTENT or
GATEWAY_MESSAGE_CONTENT_LIMITED, else guild messages arrive with empty content. Offline is an
honest `warn` skip. No secret value ever appears in a row.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import yaml

from auoreo.core import oauth_login as oauth
from auoreo.core.diagnostics import Check
from auoreo.core.paths import bundled_root
from auoreo.core.secrets import SecretStore
from auoreo.core.settings import Settings
from auoreo.harness.discovery import default_roots

DISCORD_APP = "https://discord.com/api/v10/applications/@me"
MESSAGE_CONTENT = (1 << 18) | (1 << 19)
PROBE_TIMEOUT_S = 3.0
INTENT_REMEDY = (
    "Discord Developer Portal → your app → Bot → Privileged Gateway Intents → enable MESSAGE CONTENT INTENT"
)


def _roots(st: Settings) -> list[Path]:
    return [r.path for r in default_roots(st.home, Path.cwd(), bundled_root(), st.extra_roots)]


def _plugin_manifest(roots: list[Path], name: str) -> dict[str, Any] | None:
    for root in roots:
        path = root / "plugins" / name / "plugin.yaml"
        if path.is_file():
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError):
                return None
            return data if isinstance(data, dict) else None
    return None


def oauth_rows(st: Settings, roots: list[Path], store: SecretStore) -> list[Check]:
    rows: list[Check] = []
    seen: set[str] = set()
    for root in roots:
        folder = root / "connectors"
        for path in sorted(folder.glob("*/oauth.yaml")) if folder.is_dir() else []:
            name = path.parent.name
            if name in seen:
                continue
            seen.add(name)
            try:
                cfg = oauth.load_config(path)
            except oauth.OAuthError as exc:
                rows.append(Check(f"oauth:{name}", "error", str(exc)[:200], f"fix {path}"))
                continue
            if cfg.client_id.startswith(oauth.PLACEHOLDER):
                rows.append(Check(f"oauth:{name}", "ok", "not configured (client_id placeholder)"))
            elif store.get(oauth.secret_name(name), by="doctor"):
                rows.append(Check(f"oauth:{name}", "ok", f"signed in (secret {oauth.secret_name(name)})"))
            else:
                remedy = f"auoreo login {name}"
                rows.append(Check(f"oauth:{name}", "warn", "configured, not signed in", remedy))
    return rows


async def discord_intent_row(
    st: Settings, roots: list[Path], store: SecretStore, transport: Any = None
) -> list[Check]:
    manifest = _plugin_manifest(roots, "discord")
    cfg = st.plugins.get("discord", {})
    if not manifest or not manifest.get("enabled") or cfg.get("connector") is False:
        return []
    token = store.get("discord/bot_token", by="doctor")
    if not token:
        return [Check("discord:intents", "warn", "no bot token: the Gateway connector cannot start",
                      "auoreo secrets set discord/bot_token")]  # fmt: skip
    try:
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_S, transport=transport) as client:
            resp = await client.get(DISCORD_APP, headers={"Authorization": f"Bot {token}"})
    except httpx.HTTPError as exc:
        return [Check("discord:intents", "warn", f"skipped: Discord unreachable ({type(exc).__name__})")]
    if resp.status_code in (401, 403):
        return [Check("discord:intents", "error", f"bot token rejected (HTTP {resp.status_code})",
                      "auoreo secrets set discord/bot_token")]  # fmt: skip
    if resp.status_code != 200:
        return [Check("discord:intents", "warn", f"skipped: HTTP {resp.status_code}")]
    flags = int(resp.json().get("flags") or 0)
    if flags & MESSAGE_CONTENT:
        return [Check("discord:intents", "ok", "MESSAGE CONTENT intent enabled")]
    return [Check("discord:intents", "error", "MESSAGE CONTENT intent is not enabled", INTENT_REMEDY)]


async def i7_checks(st: Settings) -> list[Check]:
    roots = _roots(st)
    store = SecretStore(st.home, st.keyring)
    return [*oauth_rows(st, roots, store), *await discord_intent_row(st, roots, store)]
