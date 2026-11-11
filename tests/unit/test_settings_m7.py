"""M7 settings scaffold: one tolerant model for every M7 knob, every feature off by default."""

from __future__ import annotations

from pathlib import Path

import pytest

from auoreo.core.settings import Settings
from auoreo.core.settings_m7 import M7Settings


def _flags(model: object, prefix: str = "") -> dict[str, object]:
    out: dict[str, object] = {}
    for name in type(model).model_fields:  # type: ignore[attr-defined]
        value = getattr(model, name)
        if hasattr(type(value), "model_fields"):
            out |= _flags(value, f"{prefix}{name}.")
        else:
            out[f"{prefix}{name}"] = value
    return out


def test_every_m7_feature_is_off_by_default(home: Path) -> None:
    m7 = Settings(home=home).m7
    flags = _flags(m7)
    switches = {k: v for k, v in flags.items() if isinstance(v, bool)}
    assert switches and not any(switches.values()), switches
    assert m7.token.cache_layout == "legacy" and m7.token.context_carrier == "splice"


def test_connector_routing_has_no_flag_and_a_stored_one_is_ignored(home: Path) -> None:
    """Routing is opt-in by `routes` in a connector's config (design m7-agents-bots, question 4)."""
    st = Settings(home=home, m7={"agents": {"connector_routes": True, "citations": True}})
    assert not hasattr(st.m7.agents, "connector_routes") and st.m7.agents.citations is True
    assert set(type(st.m7.agents).model_fields) == {"memory_tenants", "citations"}


def test_unknown_keys_are_ignored_so_a_rollback_never_breaks_startup(home: Path) -> None:
    st = Settings(home=home, m7={"token": {"telemetry": True, "from_a_newer_build": 1}, "future": {}})
    assert st.m7.token.telemetry is True
    assert M7Settings.model_validate({"orch": {"nope": True}}).orch.session_cwd is False


def test_m7_loads_from_the_real_environment_as_json(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUOREO_M7", '{"token": {"cache_layout": "tail"}, "ssh": {"hosts": true}}')
    st = Settings(home=home)
    assert st.m7.token.cache_layout == "tail" and st.m7.ssh.hosts is True


def test_closed_choices_reject_typos(home: Path) -> None:
    with pytest.raises(ValueError):
        Settings(home=home, m7={"token": {"cache_layout": "tial"}})


def test_mask_settings_defaults_and_tolerance(home: Path) -> None:
    mask = Settings(home=home).m7.token.mask
    assert (mask.keep_recent, mask.min_chars, mask.min_tokens) == (10, 2000, 8000)
    assert (mask.cache_read_multiplier, mask.cache_write_multiplier) == (0.1, None)
    st = Settings(home=home, m7={"token": {"mask": {"keep_recent": 4, "horizon_steps": 8}}})
    assert st.m7.token.mask.keep_recent == 4 and not hasattr(st.m7.token.mask, "horizon_steps")
