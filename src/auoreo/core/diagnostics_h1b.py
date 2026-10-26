"""H1b doctor rows: keyring tier (`{kind, viable, headless}` + policy), plugins still in
quarantine, and the `/ws` auth mode on exposed binds (spec §25.14, §25.15, §18)."""

from __future__ import annotations

from auoreo.core.diagnostics import Check
from auoreo.core.keyring_policy import doctor_row
from auoreo.core.settings import Settings


def _plugin_rows(st: Settings) -> list[Check]:
    from auoreo.plugins.quarantine import quarantined_names, read_marker

    out: list[Check] = []
    for name in quarantined_names(st.home):
        marker = read_marker(st.home, name) or {}
        state = "verified, not enabled" if marker.get("verified") else "not verified"
        nxt = f"auoreo plugin enable {name}" if marker.get("verified") else f"auoreo plugin verify {name}"
        out.append(Check(f"plugin_quarantine:{name}", "warn", f"quarantined ({state})", f"run `{nxt}`"))
    return out


def _ws_row(st: Settings) -> Check:
    mode = st.cli.ws_auth
    if not st.is_loopback and (mode == "off" or not st.server_password):
        return Check(
            "ws_auth", "error", f"mode={mode} on a public bind without auth", "set AUOREO_SERVER_PASSWORD"
        )
    return Check("ws_auth", "ok", f"mode={mode}; browser cookie = session JWT (ttl {st.h1b.jwt_ttl_s}s)")


async def h1b_checks(st: Settings) -> list[Check]:
    return [Check(*doctor_row(st)), _ws_row(st), *_plugin_rows(st)]  # type: ignore[arg-type]
