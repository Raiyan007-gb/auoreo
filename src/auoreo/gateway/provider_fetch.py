"""List a provider's models with one key (httpx; no `litellm`). Parsers are dict-dispatched.

`list_models(spec, key, …)` returns normalised `ModelInfo` rows (prices in USD per 1M tokens,
`kind` chat | embedding | other) or raises `ProviderFetchError(status, message)` with the
provider's own error text, redacted and with the key itself removed. `status` is `invalid` for
a rejected key (401/403) and `error` for anything else (network, 5xx, unexpected body).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx

from auoreo.core.redaction import redact
from auoreo.gateway.providers import ProviderSpec

TIMEOUT_S = 15.0
MAX_PAGES = 20
_EMBED = re.compile(r"embed|bge-|e5-|gte-|nomic-bert", re.I)
_OTHER = re.compile(
    r"whisper|tts|dall-e|gpt-image|image-|moderation|transcribe|realtime|audio|rerank|guard|"
    r"davinci|babbage|computer-use|search-preview|sora|veo|imagen|lyria|aqa",
    re.I,
)


class ProviderFetchError(RuntimeError):
    def __init__(self, status: str, message: str, http_status: int | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.http_status = http_status


@dataclass
class ModelInfo:
    id: str
    name: str = ""
    kind: str = "chat"
    context_window: int | None = None
    input_price: float | None = None
    output_price: float | None = None
    capabilities: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


def guess_kind(model_id: str) -> str:
    if _EMBED.search(model_id):
        return "embedding"
    return "other" if _OTHER.search(model_id) else "chat"


def _int(v: Any) -> int | None:
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _per_m(v: Any, scale: float = 1.0) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return round(f * scale, 6) if f >= 0 else None


# ------------------------------------------------------------------ parsers
def _openai(body: Any) -> list[ModelInfo]:
    rows = body.get("data") if isinstance(body, dict) else body
    out: list[ModelInfo] = []
    for m in rows or []:
        if not isinstance(m, dict) or not m.get("id"):
            continue
        mid = str(m["id"])
        ctx = _int(m.get("context_window") or m.get("context_length") or m.get("max_model_len"))
        out.append(ModelInfo(mid, str(m.get("name") or mid), guess_kind(mid), ctx, meta=_meta(m)))
    return out


def _meta(m: dict[str, Any]) -> dict[str, Any]:
    return {k: m[k] for k in ("owned_by", "created", "type") if k in m}


def _anthropic(body: Any) -> list[ModelInfo]:
    return [
        ModelInfo(str(m["id"]), str(m.get("display_name") or m["id"]), "chat", meta=_meta(m))
        for m in body.get("data") or []
        if isinstance(m, dict) and m.get("id")
    ]


def _openrouter(body: Any) -> list[ModelInfo]:
    out: list[ModelInfo] = []
    for m in body.get("data") or []:
        if not isinstance(m, dict) or not m.get("id"):
            continue
        arch = m.get("architecture") or {}
        outs = [str(x) for x in arch.get("output_modalities") or []]
        kind = "embedding" if "embeddings" in outs else ("chat" if not outs or "text" in outs else "other")
        if kind == "chat" and _EMBED.search(str(m["id"])):
            kind = "embedding"
        price = m.get("pricing") or {}
        caps = [str(p) for p in m.get("supported_parameters") or [] if p in {"tools", "reasoning"}]
        caps += ["vision"] if "image" in (arch.get("input_modalities") or []) else []
        out.append(
            ModelInfo(
                str(m["id"]),
                str(m.get("name") or m["id"]),
                kind,
                _int(m.get("context_length")),
                _per_m(price.get("prompt"), 1e6),
                _per_m(price.get("completion"), 1e6),
                caps,
            )
        )
    return out


def _gemini(body: Any) -> list[ModelInfo]:
    out: list[ModelInfo] = []
    for m in body.get("models") or []:
        if not isinstance(m, dict) or not m.get("name"):
            continue
        mid = str(m["name"]).removeprefix("models/")
        methods = [str(x) for x in m.get("supportedGenerationMethods") or []]
        if "generateContent" in methods:
            kind = "chat" if not _OTHER.search(mid) else "other"
        elif "embedContent" in methods or "embedText" in methods:
            kind = "embedding"
        else:
            kind = "other"
        name = str(m.get("displayName") or mid)
        out.append(ModelInfo(mid, name, kind, _int(m.get("inputTokenLimit")), capabilities=methods))
    return out


def _mistral(body: Any) -> list[ModelInfo]:
    out: list[ModelInfo] = []
    for m in body.get("data") or []:
        if not isinstance(m, dict) or not m.get("id"):
            continue
        caps = m.get("capabilities") or {}
        mid = str(m["id"])
        kind = "chat" if caps.get("completion_chat") else guess_kind(mid)
        if kind == "chat" and _EMBED.search(mid):
            kind = "embedding"
        flags = [k for k, v in caps.items() if v is True]
        out.append(
            ModelInfo(
                mid, str(m.get("name") or mid), kind, _int(m.get("max_context_length")), capabilities=flags
            )
        )
    return out


def _together(body: Any) -> list[ModelInfo]:
    rows = body.get("data") if isinstance(body, dict) else body
    kinds = {"chat": "chat", "language": "chat", "code": "chat", "embedding": "embedding"}
    out: list[ModelInfo] = []
    for m in rows or []:
        if not isinstance(m, dict) or not m.get("id"):
            continue
        price = m.get("pricing") or {}
        mid = str(m["id"])
        kind = kinds.get(str(m.get("type") or ""), "other") if m.get("type") else guess_kind(mid)
        name = str(m.get("display_name") or mid)
        ctx = _int(m.get("context_length"))
        out.append(ModelInfo(mid, name, kind, ctx, _per_m(price.get("input")), _per_m(price.get("output"))))
    return out


def _cohere(body: Any) -> list[ModelInfo]:
    out: list[ModelInfo] = []
    for m in body.get("models") or []:
        if not isinstance(m, dict) or not m.get("name"):
            continue
        eps = [str(e) for e in m.get("endpoints") or []]
        kind = "chat" if "chat" in eps else ("embedding" if "embed" in eps else "other")
        out.append(
            ModelInfo(str(m["name"]), str(m["name"]), kind, _int(m.get("context_length")), capabilities=eps)
        )
    return out


def _ollama(body: Any) -> list[ModelInfo]:
    out: list[ModelInfo] = []
    for m in body.get("models") or []:
        if not isinstance(m, dict) or not (m.get("model") or m.get("name")):
            continue
        mid = str(m.get("model") or m["name"])
        details = m.get("details") or {}
        fams = " ".join(str(f) for f in details.get("families") or [details.get("family") or ""])
        kind = "embedding" if _EMBED.search(f"{mid} {fams}") or "bert" in fams else "chat"
        out.append(ModelInfo(mid, str(m.get("name") or mid), kind, meta={"details": details}))
    return out


def _azure(body: Any) -> list[ModelInfo]:
    return []  # the key is validated; deployments come from the user


PARSERS: dict[str, Callable[[Any], list[ModelInfo]]] = {
    "openai": _openai,
    "anthropic": _anthropic,
    "openrouter": _openrouter,
    "gemini": _gemini,
    "mistral": _mistral,
    "together": _together,
    "cohere": _cohere,
    "ollama": _ollama,
    "azure": _azure,
}


# ------------------------------------------------------------------ HTTP
def _url(
    spec: ProviderSpec, url: str, base_url: str | None, version: str | None, override: str | None
) -> str:
    base = (base_url or spec.default_base_url or "").rstrip("/")
    url = url.format(base=base, version=version or spec.default_api_version or "")
    if override:
        parts = urlsplit(url)
        url = override.rstrip("/") + parts.path + (f"?{parts.query}" if parts.query else "")
    return url


def _auth(spec: ProviderSpec, key: str | None) -> tuple[dict[str, str], dict[str, str]]:
    headers: dict[str, str] = dict(spec.extra_headers)
    params: dict[str, str] = {}
    if key:
        styles: dict[str, Callable[[], None]] = {
            "bearer": lambda: headers.update(Authorization=f"Bearer {key}"),
            "x-api-key": lambda: headers.update({"x-api-key": key}),
            "api-key": lambda: headers.update({"api-key": key}),
            "query-key": lambda: params.update(key=key),
            "none": lambda: None,
        }
        styles[spec.auth]()
    return headers, params


def error_text(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:500] or f"HTTP {resp.status_code}"
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):
        return str(err.get("message") or err.get("status") or err)
    if isinstance(body, dict):
        return str(err or body.get("message") or body.get("detail") or body)[:500]
    return str(body)[:500]


def clean(message: str, key: str | None) -> str:
    if key:
        message = message.replace(key, "***")
    return redact(message)[:1000]


async def _get(
    client: httpx.AsyncClient, url: str, headers: dict[str, str], params: dict[str, str], key: str | None
) -> Any:
    try:
        resp = await client.get(httpx.URL(url).copy_merge_params(params), headers=headers)
    except httpx.HTTPError as exc:
        raise ProviderFetchError("error", clean(f"cannot reach {urlsplit(url).netloc}: {exc}", key)) from exc
    if resp.status_code in (401, 403):
        raise ProviderFetchError("invalid", clean(error_text(resp), key), resp.status_code)
    if resp.status_code >= 400:
        msg = f"HTTP {resp.status_code}: {error_text(resp)}"
        raise ProviderFetchError("error", clean(msg, key), resp.status_code)
    try:
        return resp.json()
    except ValueError as exc:
        raise ProviderFetchError("error", "the provider answered with something that is not JSON") from exc


_NEXT: dict[str, Callable[[Any], dict[str, str] | None]] = {
    "anthropic": lambda b: (
        {"after_id": str(b["last_id"])} if b.get("has_more") and b.get("last_id") else None
    ),
    "gemini": lambda b: {"pageToken": str(b["nextPageToken"])} if b.get("nextPageToken") else None,
    "cohere": lambda b: {"page_token": str(b["next_page_token"])} if b.get("next_page_token") else None,
}
_FIRST: dict[str, dict[str, str]] = {
    "anthropic": {"limit": "1000"},
    "gemini": {"pageSize": "1000"},
    "cohere": {"page_size": "1000"},
}


async def list_models(
    spec: ProviderSpec,
    key: str | None,
    *,
    base_url: str | None = None,
    api_version: str | None = None,
    override: str | None = None,
    client: httpx.AsyncClient | None = None,
    timeout_s: float = TIMEOUT_S,
) -> list[ModelInfo]:
    """Validate `key` and return the provider's models (all pages)."""
    if spec.needs_key and not key:
        raise ProviderFetchError("invalid", f"{spec.name} needs an API key")
    if spec.needs_base_url and not (base_url or spec.default_base_url):
        raise ProviderFetchError("error", f"{spec.name} needs an endpoint URL")
    owns = client is None
    client = client or httpx.AsyncClient(timeout=timeout_s, follow_redirects=False)
    headers, params = _auth(spec, key)
    try:
        if spec.validate_url:
            await _get(
                client, _url(spec, spec.validate_url, base_url, api_version, override), headers, params, key
            )
        url = _url(spec, spec.list_url, base_url, api_version, override)
        page = {**params, **_FIRST.get(spec.parser, {})}
        out: list[ModelInfo] = []
        for _ in range(MAX_PAGES):
            body = await _get(client, url, headers, page, key)
            if not isinstance(body, dict | list):
                raise ProviderFetchError("error", "unexpected model list format")
            out += PARSERS[spec.parser](body)
            nxt = _NEXT.get(spec.parser, lambda b: None)(body) if isinstance(body, dict) else None
            if not nxt:
                break
            page = {**params, **_FIRST.get(spec.parser, {}), **nxt}
    finally:
        if owns:
            await client.aclose()
    return list({m.id: m for m in out}.values())


__all__ = ["PARSERS", "ModelInfo", "ProviderFetchError", "clean", "guess_kind", "list_models"]
