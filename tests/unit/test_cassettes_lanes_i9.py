"""I9 (spec §25.9 L1008, §20.4): one replay cassette per gateway lane under
`tests/fixtures/cassettes/<provider>/<case>.yaml` (openai, anthropic, openrouter; hand-authored
in each vendor's documented SSE shape, secrets filtered). The REAL LiteLLM Router streams each
cassette through `LiteLLMGateway.stream`; record mode `none`, so no network and no key.

Re-record one lane explicitly (key in the env, filtered on write):
    VCR_RECORD=1 OPENAI_API_KEY=... uv run pytest tests/unit/test_cassettes_lanes_i9.py -k openai
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import vcr
import yaml

from auoreo.core.settings import ModelGroup, Settings
from auoreo.gateway.gateway import LiteLLMGateway
from auoreo.gateway.types import LLMRequest, TextDelta, Usage
from tests import cassettes

LANES = {
    "openai": ("openai/gpt-4o-mini", "hello from the openai cassette"),
    "anthropic": ("anthropic/claude-sonnet-4-5", "hello from the anthropic cassette"),
    "openrouter": ("openrouter/anthropic/claude-sonnet-4.5", "hello from the openrouter cassette"),
}


def lane_vcr(provider: str) -> vcr.VCR:
    cfg = cassettes.config()
    cfg["match_on"] = ["method", "scheme", "host", "port", "path"]  # bodies are LiteLLM-built
    record = "once" if os.environ.get("VCR_RECORD") == "1" else "none"
    return vcr.VCR(cassette_library_dir=str(cassettes.CASSETTES / provider), record_mode=record, **cfg)


@pytest.mark.parametrize("provider", sorted(LANES))
async def test_lane_replays_its_cassette(home: Path, provider: str) -> None:
    model, expected = LANES[provider]
    st = Settings(home=home, models={"default": ModelGroup(models=[model])}, fallbacks={})
    gw = LiteLLMGateway(st, lambda name: os.environ.get(name) or "sk-replay-placeholder")
    with lane_vcr(provider).use_cassette("stream_text.yaml") as cas:
        items = [
            i
            async for i in gw.stream(
                LLMRequest(model="default", messages=({"role": "user", "content": "say hello"},))
            )
        ]
    assert cas.play_count == 1
    assert "".join(i.text for i in items if isinstance(i, TextDelta)) == expected
    usage = [i for i in items if isinstance(i, Usage)]
    assert usage and usage[-1].completion_tokens > 0


def test_lane_cassettes_are_filtered() -> None:
    for provider in LANES:
        path = cassettes.CASSETTES / provider / "stream_text.yaml"
        doc = yaml.safe_load(path.read_text())
        for it in doc["interactions"]:
            headers = {k.lower(): v for k, v in it["request"]["headers"].items()}
            for h in ("authorization", "x-api-key"):
                assert headers.get(h, ["REDACTED"]) == ["REDACTED"], (provider, h)
            assert "sk-" not in it["response"]["body"]["string"]
