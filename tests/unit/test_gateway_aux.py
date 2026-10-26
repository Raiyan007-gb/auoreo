"""Aux routing (G3, spec §10.7): task pin → chain with credentials → small → main."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from auoreo.core.settings import ModelGroup, Settings
from auoreo.core.settings_gateway import AuxSettings
from auoreo.gateway import auxiliary as aux
from auoreo.gateway.auxiliary import AUX_TASKS, resolve_aux_model
from auoreo.gateway.fake import FakeGateway


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    aux.clear_failures()
    yield
    aux.clear_failures()


def _settings(home, **aux_cfg) -> Settings:  # type: ignore[no-untyped-def]
    return Settings(
        home=home,
        models={
            "default": ModelGroup(models=["openrouter/anthropic/claude-sonnet-4.5"]),
            "small": ModelGroup(models=["openrouter/openai/gpt-4o-mini"]),
            "cheap": ModelGroup(models=["anthropic/claude-haiku-5-5"]),
        },
        auxiliary=AuxSettings(**aux_cfg),
    )


def test_defaults_route_every_task_to_small(home) -> None:  # type: ignore[no-untyped-def]
    st = _settings(home)
    assert {t: resolve_aux_model(st, t) for t in AUX_TASKS} == dict.fromkeys(AUX_TASKS, "small")


def test_no_small_group_falls_back_to_main(home) -> None:  # type: ignore[no-untyped-def]
    st = Settings(home=home, models={"default": ModelGroup(models=["a/b"])})
    assert resolve_aux_model(st, "title") == "default"
    assert resolve_aux_model(st, "title", main="x/session-model") == "x/session-model"


def test_task_pin_wins_and_expands_aliases(home) -> None:  # type: ignore[no-untyped-def]
    st = _settings(home, tasks={"guardian": "cheap", "hyde": "haiku"}, text_chain=["default"])
    assert resolve_aux_model(st, "guardian") == "cheap"
    assert resolve_aux_model(st, "hyde") == "openrouter/anthropic/claude-haiku-5.5"
    assert resolve_aux_model(st, "title") == "default"  # chain, not small


def test_chain_skips_entries_without_credentials_and_recent_failures(home) -> None:  # type: ignore[no-untyped-def]
    gw = FakeGateway()
    gw.missing_credentials = {"anthropic/claude-haiku-5-5"}
    st = _settings(home, text_chain=["cheap", "openrouter/google/gemini-flash", "main"])
    assert resolve_aux_model(st, "compaction", gateway=gw) == "openrouter/google/gemini-flash"
    aux.note_failure("openrouter/google/gemini-flash")  # lazy probe: skip it for probe_ttl_s
    assert resolve_aux_model(st, "compaction", gateway=gw, main="x/main") == "x/main"
    off = _settings(home, text_chain=["openrouter/google/gemini-flash"], aux_probe_mode="off")
    assert resolve_aux_model(off, "compaction", gateway=gw) == "openrouter/google/gemini-flash"


def test_free_only_and_vision_chain(home) -> None:  # type: ignore[no-untyped-def]
    st = _settings(home, text_chain=["openrouter/meta/llama"], vision_chain=["main", "small"], free_only=True)
    assert resolve_aux_model(st, "summary") == "openrouter/meta/llama:free"
    gw = FakeGateway()
    gw.vision = False
    assert resolve_aux_model(st, "summary", vision=True, gateway=gw) == "small"  # nothing passes the gate
    gw.vision = True
    assert resolve_aux_model(st, "summary", vision=True, gateway=gw) == "default"


def test_background_purposes_cover_aux_tasks() -> None:
    assert set(AUX_TASKS) | {"curator"} <= aux.BACKGROUND_PURPOSES
