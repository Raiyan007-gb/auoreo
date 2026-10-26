"""Prefix parity (spec §19.10, §25.11): every provider key format named in `.env.example`, the
spec prefix list, and every secret name declared by tools/backends/plugins is redacted.
CI fails on drift: a new provider key in `.env.example` needs a sample format here."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from auoreo.core.redaction import ALL_PREFIXES, redact

ROOT = Path(__file__).resolve().parents[2]

# Spec §19.10 / §25.11 prefix list (the "~45" table, minus generic families covered by `sk-`).
SPEC_PREFIXES = (
    "sk-",
    "sk-or-",
    "sk-ant-",
    "ghp_",
    "github_pat_",
    "gho_",
    "ghu_",
    "ghs_",
    "ghr_",
    "xoxb-",
    "xapp-",
    "AIza",
    "pplx-",
    "exa_",
    "gsk_",
    "tvly-",
    "hf_",
    "AKIA",
    "SG.",
    "gAAAA",
    "glpat-",
    "xai-",
    "fw-",
    "fpk_",
    "eyJ",
    "dsk-",
    "llm-",
    "r1-",
    "oai-",
)

# One realistic sample per provider key in `.env.example` (shape only, never a real key).
PROVIDER_SAMPLES = {
    "OPENROUTER_API_KEY": "sk-or-v1-0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d",
    "ANTHROPIC_API_KEY": "sk-ant-api03-AbCdEfGhIjKlMnOpQrStUv",
    "OPENAI_API_KEY": "sk-proj-AbCdEfGhIjKlMnOpQrStUvWx",
    "GEMINI_API_KEY": "AIzaSyA1b2C3d4E5f6G7h8I9j0K1l2M3n4",
    "GROQ_API_KEY": "gsk_AbCdEfGhIjKlMnOpQrStUvWxYz012345",
    "XAI_API_KEY": "xai-AbCdEfGhIjKlMnOpQrStUvWxYz",
    "MISTRAL_API_KEY": "Zq8Rk2Vt6Yw0Bn4Mc7Lp1Xs5Df9Gh3Jk",
    "DEEPSEEK_API_KEY": "sk-0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d",
    "TOGETHERAI_API_KEY": "tgp_v1_AbCdEfGhIjKlMnOpQrStUvWxYz",
    "FIREWORKS_AI_API_KEY": "fw-AbCdEfGhIjKlMnOpQrStUv",
    "AZURE_API_KEY": "0123456789abcdef0123456789abcdef",
    # tool / backend keys (search, rerank, voice, environments, LiveKit)
    "COHERE_API_KEY": "Zx9Kq2Lm4Np6Rt8Vw0Yb3Dc5Fg7Hj1Ks9Qa2We4",
    "EXA_API_KEY": "3f2a9c1e-7b4d-4e8a-9c2f-1a6b5d8e0f3c",
    "PARALLEL_API_KEY": "pk7Qw2Er4Ty6Ui8Op0As3Df5Gh7Jk9Lz",
    "BRAVE_API_KEY": "BSAq1W2e3R4t5Y6u7I8o9P0aSdFgHjKl",
    "TAVILY_API_KEY": "tvly-dev-AbCdEfGhIjKlMnOpQrStUvWx",
    "JINA_API_KEY": "jina_0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5dAbCdEf",
    "FIRECRAWL_API_KEY": "fc-0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d",
    "DEEPGRAM_API_KEY": "0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b",
    "DEEPINFRA_API_KEY": "Dq3Wr5Ty7Ui9Op1As3Df5Gh7Jk9Lz2Xc",
    "ELEVENLABS_API_KEY": "sk_0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f",
    "E2B_API_KEY": "e2b_0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b",
    "DAYTONA_API_KEY": "dtn_0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b",
    "LIVEKIT_API_KEY": "APIq1W2e3R4t5Y6u",
}
KEYISH = re.compile(r"\b([A-Z][A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD|_KEY))\b")


def _env_example_names() -> set[str]:
    return set(KEYISH.findall((ROOT / ".env.example").read_text(encoding="utf-8")))


def _declared_secret_names() -> set[str]:
    """Secret-ish env names any tool/backend/plugin manifest mentions (requires_env, secrets:,
    descriptions) — what ends up in `secrets.db` or the scrubbed child env."""
    names: set[str] = set()
    for pattern in ("tools/*/tool.yaml", "backends/*/*/backend.yaml", "plugins/*/plugin.yaml"):
        for path in ROOT.glob(pattern):
            text = path.read_text(encoding="utf-8")
            yaml.safe_load(text)  # manifests stay parseable
            names |= set(KEYISH.findall(text))
    return names


def test_spec_prefixes_are_known() -> None:
    missing = [p for p in SPEC_PREFIXES if p not in ALL_PREFIXES]
    assert not missing, f"redaction lacks spec prefixes: {missing}"
    assert all(len(p) >= 2 and p[:2].isascii() for p in ALL_PREFIXES)


@pytest.mark.parametrize("prefix", SPEC_PREFIXES)
def test_each_prefix_is_redacted(prefix: str) -> None:
    token = prefix + "a1B2c3D4e5F6g7H8i9J0"
    for mode in ("mask", "sentinel"):
        out = redact(f"value {token} end", mode)  # type: ignore[arg-type]
        assert token not in out, (prefix, mode, out)


def test_every_env_example_provider_has_a_sample() -> None:
    names = {n for n in _env_example_names() if n.endswith("_API_KEY")}
    missing = names - PROVIDER_SAMPLES.keys()
    assert not missing, f"add a sample key format for {sorted(missing)} (and a redaction prefix if needed)"


@pytest.mark.parametrize(("name", "sample"), sorted(PROVIDER_SAMPLES.items()))
def test_provider_samples_redacted_bare_and_assigned(name: str, sample: str) -> None:
    assert sample not in redact(f"{name}={sample}")
    assert sample not in redact(f'{{"{name.lower()}": "{sample}"}}')
    if any(sample.startswith(p) for p in ALL_PREFIXES):
        assert sample not in redact(f"curl -H 'x: {sample}'"), "bare key with a known prefix leaks"


def test_declared_secret_names_redacted_as_assignments() -> None:
    names = _declared_secret_names() | _env_example_names()
    assert {"GITHUB_TOKEN", "OPENAI_API_KEY", "GH_TOKEN", "MODAL_TOKEN_SECRET"} <= names
    for name in sorted(names):
        value = "Q7w8E9r0T1y2U3i4O5p6"
        assert value not in redact(f"{name}={value}"), name
        assert value not in redact(f"export {name}='{value}'"), name
