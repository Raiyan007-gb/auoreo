"""imagegen plugin (F7 spike), in process: manifest posture, fake backend, and contract tests
for the OpenAI Images / fal request shapes over `httpx.MockTransport` (recorded requests)."""

from __future__ import annotations

import base64
import importlib.util
import io
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest
import yaml
from PIL import Image

from auoreo.harness.manifests import PluginManifest
from auoreo.plugins.bridge import make_tool

PLUGIN_DIR = Path(__file__).resolve().parents[2] / "plugins" / "imagegen"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("imagegen_plugin_under_test", PLUGIN_DIR / "plugin.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ig = load()


def png(w: int = 8, h: int = 8) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (10, 200, 30)).save(buf, "PNG")
    return buf.getvalue()


def read(path: str | Path) -> bytes:
    return Path(path).read_bytes()


def resolved(path: str | Path) -> Path:
    return Path(path).resolve()


class FakeHost:
    def __init__(self, secrets: dict[str, str] | None = None) -> None:
        self.secrets = secrets or {}
        self.asked: list[str] = []
        self.logs: list[tuple[str, str]] = []

    async def secret(self, name: str) -> str | None:
        self.asked.append(name)
        return self.secrets.get(name)

    def log(self, level: str, message: str) -> None:
        self.logs.append((level, message))


class Recorder:
    """A MockTransport handler that records every request and answers from a route table."""

    def __init__(self, routes: dict[tuple[str, str], httpx.Response]) -> None:
        self.routes = routes
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.routes.get(
            (request.method, str(request.url)), httpx.Response(404, json={"error": "no route"})
        )


def plugin(tmp_path: Path, config: dict[str, Any], rec: Recorder | None = None, **secrets: str) -> Any:
    p = ig.Plugin(FakeHost(secrets), transport=httpx.MockTransport(rec) if rec else None)
    info = p.initialize({"output_dir": str(tmp_path / "out"), **config}, [])
    assert [t["name"] for t in info["tools"]] == ["imagegen_generate"]
    return p


# ------------------------------------------------------------------ manifest
def test_manifest_is_off_by_default_tools_only_and_declares_both_secrets() -> None:
    m = PluginManifest.model_validate(yaml.safe_load((PLUGIN_DIR / "plugin.yaml").read_text()))
    assert m.enabled is False
    assert m.provides == ["tools"]
    assert set(m.consumes) == {"secrets:OPENAI_API_KEY", "secrets:FAL_KEY", "usage"}
    assert m.permissions == ["plugin__imagegen__imagegen_generate"]
    assert m.config_schema["properties"]["backend"]["default"] == "fake"


def test_bridged_tool_asks_for_approval_and_caps_n() -> None:
    async def never(*_a: Any) -> Any:
        raise AssertionError

    tool = make_tool("imagegen", ig.TOOLS[0], never, default_timeout_s=60)
    assert tool.manifest.name == "plugin__imagegen__imagegen_generate"
    assert tool.manifest.default_action == "ask"
    assert tool.manifest.parameters["properties"]["n"]["maximum"] == 2


# ---------------------------------------------------------------- fake
async def test_fake_backend_writes_pngs_under_the_session_folder_and_records_cost(tmp_path: Path) -> None:
    p = plugin(tmp_path, {})
    res = await p.call_tool("imagegen_generate", {"prompt": "a red mug", "n": 2}, "ses_abc")
    assert res["is_error"] is False
    out = json.loads(res["content"])
    assert out["backend"] == "fake" and out["usd_estimate"] == 0.0
    folder = tmp_path / "out" / "ses_abc" / "imagegen"
    assert len(out["images"]) == 2
    for img in out["images"]:
        assert Path(img["path"]).parent == folder and read(img["path"]).startswith(b"\x89PNG")
        assert Image.open(io.BytesIO(read(img["path"]))).size == (256, 256)
    rows = [json.loads(line) for line in (folder / "costs.jsonl").read_text().splitlines()]
    assert rows[0]["usd_estimate"] == 0.0 and rows[0]["n"] == 2
    assert any(msg.startswith("imagegen.cost ") for _lvl, msg in p.host.logs)
    assert p.host.asked == []  # the fake backend never asks for secrets


@pytest.mark.parametrize(
    ("args", "needle"),
    [
        ({"prompt": "x", "n": 3}, "n must be"),
        ({"prompt": "x", "size": "512x512"}, "size must be"),
        ({"prompt": "  "}, "prompt is empty"),
    ],
)
async def test_bad_arguments_are_tool_errors(tmp_path: Path, args: dict[str, Any], needle: str) -> None:
    res = await plugin(tmp_path, {}).call_tool("imagegen_generate", args, "s")
    assert res["is_error"] is True and needle in res["content"]


async def test_session_ids_cannot_escape_the_output_folder(tmp_path: Path) -> None:
    res = await plugin(tmp_path, {}).call_tool("imagegen_generate", {"prompt": "x"}, "../../etc")
    path = json.loads(res["content"])["images"][0]["path"]
    assert resolved(path).is_relative_to(resolved(tmp_path / "out"))


# --------------------------------------------------------------- openai
OPENAI_URL = "https://api.openai.com/v1/images/generations"


async def test_openai_request_shape_and_token_cost(tmp_path: Path) -> None:
    b64 = base64.b64encode(png()).decode()
    body = {
        "created": 1,
        "data": [{"b64_json": b64}, {"b64_json": b64}],
        "usage": {"input_tokens": 50, "output_tokens": 1000, "total_tokens": 1050},
    }
    rec = Recorder({("POST", OPENAI_URL): httpx.Response(200, json=body)})
    p = plugin(tmp_path, {"backend": "openai"}, rec, OPENAI_API_KEY="sk-test")
    res = await p.call_tool("imagegen_generate", {"prompt": "a cat", "size": "1536x1024", "n": 2}, "s1")
    assert res["is_error"] is False, res
    (req,) = rec.requests
    assert req.headers["authorization"] == "Bearer sk-test"
    assert json.loads(req.content) == {
        "model": "gpt-image-1",
        "prompt": "a cat",
        "size": "1536x1024",
        "n": 2,
        "quality": "low",
    }
    out = json.loads(res["content"])
    assert out["model"] == "gpt-image-1" and len(out["images"]) == 2
    assert out["usd_estimate"] == pytest.approx(50 * 5e-6 + 1000 * 40e-6)
    assert "usage tokens" in out["cost_basis"]
    assert p.host.asked == ["OPENAI_API_KEY"]


async def test_openai_without_usage_falls_back_to_the_per_image_table(tmp_path: Path) -> None:
    b64 = base64.b64encode(png()).decode()
    rec = Recorder({("POST", OPENAI_URL): httpx.Response(200, json={"data": [{"b64_json": b64}]})})
    p = plugin(tmp_path, {"backend": "openai", "openai_quality": "medium"}, rec, OPENAI_API_KEY="k")
    out = json.loads((await p.call_tool("imagegen_generate", {"prompt": "x"}, "s"))["content"])
    assert out["usd_estimate"] == pytest.approx(0.042)


async def test_openai_errors_and_missing_key_are_tool_errors(tmp_path: Path) -> None:
    rec = Recorder(
        {("POST", OPENAI_URL): httpx.Response(401, json={"error": {"message": "Incorrect API key"}})}
    )
    res = await plugin(tmp_path, {"backend": "openai"}, rec, OPENAI_API_KEY="bad").call_tool(
        "imagegen_generate", {"prompt": "x"}, "s"
    )
    assert res["is_error"] and "HTTP 401" in res["content"] and "Incorrect API key" in res["content"]
    res = await plugin(tmp_path, {"backend": "openai"}, rec).call_tool(
        "imagegen_generate", {"prompt": "x"}, "s"
    )
    assert res["is_error"] and "OPENAI_API_KEY is not set" in res["content"]


async def test_non_image_payloads_are_refused(tmp_path: Path) -> None:
    b64 = base64.b64encode(b"<svg onload=alert(1)>").decode()
    rec = Recorder({("POST", OPENAI_URL): httpx.Response(200, json={"data": [{"b64_json": b64}]})})
    res = await plugin(tmp_path, {"backend": "openai"}, rec, OPENAI_API_KEY="k").call_tool(
        "imagegen_generate", {"prompt": "x"}, "s"
    )
    assert res["is_error"] and "not a PNG/JPEG/WebP" in res["content"]
    assert not (tmp_path / "out" / "s" / "imagegen" / "costs.jsonl").exists()


# ------------------------------------------------------------------ fal
FAL_URL = "https://fal.run/fal-ai/flux/schnell"
FAL_IMG = "https://v3.fal.media/files/abc/image.png"


async def test_fal_request_shape_download_and_megapixel_cost(tmp_path: Path) -> None:
    answer = {"images": [{"url": FAL_IMG, "width": 1024, "height": 1024, "content_type": "image/png"}]}
    rec = Recorder(
        {
            ("POST", FAL_URL): httpx.Response(200, json=answer),
            ("GET", FAL_IMG): httpx.Response(200, content=png()),
        }
    )
    p = plugin(tmp_path, {"backend": "fal"}, rec, FAL_KEY="fal-test")
    res = await p.call_tool("imagegen_generate", {"prompt": "a dog"}, "s2")
    assert res["is_error"] is False, res
    post, get = rec.requests
    assert post.headers["authorization"] == "Key fal-test"
    assert json.loads(post.content) == {
        "prompt": "a dog",
        "image_size": {"width": 1024, "height": 1024},
        "num_images": 1,
    }
    assert str(get.url) == FAL_IMG and "authorization" not in get.headers
    out = json.loads(res["content"])
    assert out["usd_estimate"] == pytest.approx(1024 * 1024 / 1e6 * 0.003, rel=1e-3)
    assert read(out["images"][0]["path"]) == png()


async def test_fal_refuses_plain_http_image_urls_and_config_price_overrides(tmp_path: Path) -> None:
    answer = {"images": [{"url": "http://insecure.example/x.png"}]}
    rec = Recorder({("POST", FAL_URL): httpx.Response(200, json=answer)})
    res = await plugin(tmp_path, {"backend": "fal"}, rec, FAL_KEY="k").call_tool(
        "imagegen_generate", {"prompt": "x"}, "s"
    )
    assert res["is_error"] and "not https" in res["content"]
    res = await plugin(tmp_path, {"usd_per_image": 0.5}).call_tool(
        "imagegen_generate", {"prompt": "x", "n": 2}, "s"
    )
    out = json.loads(res["content"])
    assert out["usd_estimate"] == 1.0 and out["cost_basis"] == "config usd_per_image"
