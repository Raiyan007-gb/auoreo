"""Image generation for AUOREO as an out-of-process plugin (F7 spike, docs/spikes/imagegen.md).

Tool `imagegen_generate{prompt, size, n<=2}` -> `plugin__imagegen__imagegen_generate`. The host
registers every plugin tool with `default_action: ask`, so each call needs approval unless a
permission rule allows that name. Backends (config `backend`):

- `openai`: `POST {openai_base_url}/images/generations` with `model` (gpt-image-1), `prompt`,
  `size`, `n`, `quality`; GPT image models answer `data[].b64_json` plus token `usage`.
- `fal`: `POST {fal_base_url}/{fal_model}` (synchronous `fal.run`), `Authorization: Key ...`,
  body `{prompt, image_size: {width, height}, num_images}`; the answer lists `images[].url`,
  which are downloaded.
- `fake`: a Pillow-drawn placeholder (no network, $0) - the default and the test backend.

Images are written to `<output_dir>/<session_id>/imagegen/` and returned as paths in the tool
result (the plugin wire protocol carries text only, so no inline image part). The estimated
cost is part of the result, sent to the host log as `imagegen.cost {...}` and appended to
`costs.jsonl` in the same folder: the host has no plugin -> usage-ledger/audit call yet.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import re
import time
from pathlib import Path
from typing import Any

import httpx

NAME = "imagegen"
SIZES = ("1024x1024", "1024x1536", "1536x1024")
MAX_N = 2
MAX_IMAGE_BYTES = 25 * 1024 * 1024
# Estimates in USD, per image. gpt-image-1 list prices (documented by OpenAI): per-token when
# the answer carries `usage`, else the per-image table. fal FLUX schnell: ~$0.003 per megapixel
# (documented by fal); other fal models use the same rate and say so in `cost_basis`.
OPENAI_TOKEN_USD = {"input": 5.0 / 1e6, "output": 40.0 / 1e6}
OPENAI_IMAGE_USD = {
    "low": {"1024x1024": 0.011, "other": 0.016},
    "medium": {"1024x1024": 0.042, "other": 0.063},
    "high": {"1024x1024": 0.167, "other": 0.25},
}
FAL_USD_PER_MP = 0.003

TOOLS: list[dict[str, Any]] = [
    {
        "name": "imagegen_generate",
        "timeout_ms": 180_000,  # hosted models can take minutes; the host default is 60 s
        "description": (
            "Generate 1-2 images from a text prompt (hosted image model; costs money, asks for "
            "approval). Returns the saved file paths and the estimated cost."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "minLength": 1, "maxLength": 4000},
                "size": {"type": "string", "enum": list(SIZES), "default": "1024x1024"},
                "n": {"type": "integer", "minimum": 1, "maximum": MAX_N, "default": 1},
            },
            "required": ["prompt"],
            "additionalProperties": False,
        },
    }
]


class GenError(Exception):
    """A backend failure shown to the model as a tool error."""


def sniff(data: bytes) -> str:
    """`png` | `jpeg` | `webp` by magic bytes; anything else is refused."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    raise GenError("backend returned something that is not a PNG/JPEG/WebP image")


def safe_session(session_id: str) -> str:
    out = re.sub(r"[^A-Za-z0-9_-]", "_", session_id or "")[:80]
    return out or "no-session"


def dims(size: str) -> tuple[int, int]:
    w, h = size.split("x")
    return int(w), int(h)


def openai_cost(quality: str, size: str, n: int, usage: dict[str, Any] | None) -> tuple[float, str]:
    if usage and usage.get("output_tokens") is not None:
        usd = int(usage.get("input_tokens") or 0) * OPENAI_TOKEN_USD["input"]
        usd += int(usage.get("output_tokens") or 0) * OPENAI_TOKEN_USD["output"]
        return usd, "openai usage tokens x list price"
    table = OPENAI_IMAGE_USD.get(quality, OPENAI_IMAGE_USD["high"])
    return n * table[
        "1024x1024" if size == "1024x1024" else "other"
    ], f"openai per-image list price ({quality})"


def fal_cost(model: str, size: str, n: int) -> tuple[float, str]:
    w, h = dims(size)
    basis = f"fal ${FAL_USD_PER_MP}/MP" + (
        "" if model == "fal-ai/flux/schnell" else f" (assumed for {model})"
    )
    return n * (w * h / 1e6) * FAL_USD_PER_MP, basis


def placeholder(prompt: str, size: str, index: int) -> bytes:
    """The fake backend: a deterministic labelled card, scaled down 4x to keep files small."""
    from PIL import Image, ImageDraw

    w, h = dims(size)
    w, h = w // 4, h // 4
    seed = sum(prompt.encode()) + 97 * index
    bg = (40 + seed % 160, 40 + (seed // 7) % 160, 40 + (seed // 49) % 160)
    img = Image.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(img)
    d.rectangle((6, 6, w - 7, h - 7), outline=(255, 255, 255), width=3)
    words, line, lines = prompt.split(), "", []
    for word in words:
        if len(line) + len(word) + 1 > 36:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    lines.append(line)
    d.text((14, 14), f"imagegen fake #{index + 1}", fill=(255, 255, 255))
    for i, text in enumerate(lines[:8]):
        d.text((14, 34 + 14 * i), text, fill=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


class Plugin:
    def __init__(self, host: Any, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.host = host
        self.transport = transport  # tests inject httpx.MockTransport
        self.config: dict[str, Any] = {}

    # ------------------------------------------------------------- protocol
    def initialize(self, config: dict[str, Any], consumes_granted: list[str]) -> dict[str, Any]:
        self.config_update(config)
        return {"tools": TOOLS, "deliveries": [], "connectors": [], "skills": []}

    def config_update(self, config: dict[str, Any]) -> dict[str, Any]:
        self.config = dict(config)
        return {}

    def cfg(self, key: str, default: Any) -> Any:
        value = self.config.get(key)
        return default if value is None else value

    async def call_tool(self, name: str, args: dict[str, Any], session_id: str) -> dict[str, Any]:
        if name != "imagegen_generate":
            return {"content": f"unknown tool {name}", "is_error": True}
        try:
            return {"content": json.dumps(await self.generate(args, session_id)), "is_error": False}
        except GenError as exc:
            return {"content": f"imagegen failed: {exc}", "is_error": True}

    # ------------------------------------------------------------- generate
    async def generate(self, args: dict[str, Any], session_id: str) -> dict[str, Any]:
        prompt = str(args.get("prompt") or "").strip()
        size = str(args.get("size") or "1024x1024")
        n = int(args.get("n") or 1)
        if not prompt:
            raise GenError("prompt is empty")
        if size not in SIZES:
            raise GenError(f"size must be one of {list(SIZES)}")
        if not 1 <= n <= MAX_N:
            raise GenError(f"n must be 1..{MAX_N}")
        backend = str(self.cfg("backend", "fake"))
        runner = {"fake": self._fake, "openai": self._openai, "fal": self._fal}.get(backend)
        if runner is None:
            raise GenError(f"unknown backend {backend!r}")
        started = time.monotonic()
        images, model, usd, basis = await runner(prompt, size, n)
        if self.config.get("usd_per_image") is not None:
            usd, basis = n * float(self.config["usd_per_image"]), "config usd_per_image"
        formats = [sniff(data) for data in images]  # refuse before anything is written
        folder = f"{self.cfg('output_dir', '~/.auoreo/sessions')}/{safe_session(session_id)}/{NAME}"
        cost = {
            "backend": backend,
            "model": model,
            "n": len(images),
            "size": size,
            "usd_estimate": round(usd, 6),
            "cost_basis": basis,
            "session_id": session_id,
            "ts": time.time(),
        }
        saved = await asyncio.to_thread(save, folder, images, formats, cost)
        self.host.log("info", "imagegen.cost " + json.dumps(cost))
        return {
            "images": saved,
            "backend": backend,
            "model": model,
            "usd_estimate": cost["usd_estimate"],
            "cost_basis": basis,
            "elapsed_s": round(time.monotonic() - started, 2),
        }

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=float(self.cfg("timeout_s", 120)), transport=self.transport)

    async def _secret(self, name: str) -> str:
        value = await self.host.secret(name)
        if not value:
            raise GenError(f"secret {name} is not set (auoreo secrets set {name})")
        return str(value)

    async def _fake(self, prompt: str, size: str, n: int) -> tuple[list[bytes], str, float, str]:
        return [placeholder(prompt, size, i) for i in range(n)], "fake-placeholder", 0.0, "fake backend"

    async def _openai(self, prompt: str, size: str, n: int) -> tuple[list[bytes], str, float, str]:
        key = await self._secret("OPENAI_API_KEY")
        model = str(self.cfg("openai_model", "gpt-image-1"))
        quality = str(self.cfg("openai_quality", "low"))
        url = (
            str(self.cfg("openai_base_url", "https://api.openai.com/v1")).rstrip("/") + "/images/generations"
        )
        body = {"model": model, "prompt": prompt, "size": size, "n": n, "quality": quality}
        async with self.client() as http:
            r = await http.post(url, json=body, headers={"Authorization": f"Bearer {key}"})
        if r.status_code != 200:
            raise GenError(f"OpenAI HTTP {r.status_code}: {_error_text(r)}")
        payload = r.json()
        images = []
        for item in payload.get("data") or []:
            b64 = item.get("b64_json")
            if not b64:
                raise GenError("OpenAI answer has no b64_json (GPT image models return base64)")
            images.append(_cap(base64.b64decode(b64)))
        if not images:
            raise GenError("OpenAI answer contains no images")
        usd, basis = openai_cost(quality, size, len(images), payload.get("usage"))
        return images, model, usd, basis

    async def _fal(self, prompt: str, size: str, n: int) -> tuple[list[bytes], str, float, str]:
        key = await self._secret("FAL_KEY")
        model = str(self.cfg("fal_model", "fal-ai/flux/schnell"))
        url = str(self.cfg("fal_base_url", "https://fal.run")).rstrip("/") + "/" + model
        w, h = dims(size)
        body = {"prompt": prompt, "image_size": {"width": w, "height": h}, "num_images": n}
        async with self.client() as http:
            r = await http.post(url, json=body, headers={"Authorization": f"Key {key}"})
            if r.status_code != 200:
                raise GenError(f"fal HTTP {r.status_code}: {_error_text(r)}")
            images = []
            for item in r.json().get("images") or []:
                src = str(item.get("url") or "")
                if src.startswith("data:"):
                    images.append(_cap(base64.b64decode(src.split(",", 1)[1])))
                    continue
                if not src.startswith("https://"):
                    raise GenError("fal image url is not https")
                got = await http.get(src)
                if got.status_code != 200:
                    raise GenError(f"fal image download HTTP {got.status_code}")
                images.append(_cap(got.content))
        if not images:
            raise GenError("fal answer contains no images")
        usd, basis = fal_cost(model, size, len(images))
        return images, model, usd, basis


def save(where: str, images: list[bytes], formats: list[str], cost: dict[str, Any]) -> list[dict[str, Any]]:
    """Write the images and append the cost row (blocking; run in a thread)."""
    folder = Path(where).expanduser()
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() // 1000 % 1_000_000:06d}"
    saved = []
    for i, (data, fmt) in enumerate(zip(images, formats, strict=True)):
        path = folder / f"{stem}-{i + 1}.{'jpg' if fmt == 'jpeg' else fmt}"
        path.write_bytes(data)
        saved.append({"path": str(path), "media_type": f"image/{fmt}", "bytes": len(data)})
    with (folder / "costs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(cost) + "\n")
    return saved


def _cap(data: bytes) -> bytes:
    if len(data) > MAX_IMAGE_BYTES:
        raise GenError(f"image is larger than {MAX_IMAGE_BYTES} bytes")
    return data


def _error_text(r: httpx.Response) -> str:
    try:
        err = r.json().get("error") or r.json().get("detail") or r.json()
    except ValueError:
        err = r.text
    return str(err.get("message", err) if isinstance(err, dict) else err)[:300]
