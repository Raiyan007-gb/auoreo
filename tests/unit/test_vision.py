"""M4a: image helpers, capture backends, vision tools and multimodal runner messages."""

from __future__ import annotations

import base64
import io
import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from PIL import Image

from auoreo.core.permissions import PermissionEngine
from auoreo.core.services import Services
from auoreo.core.settings import ModelGroup, Settings
from auoreo.gateway.fake import FakeGateway, Scripted
from auoreo.gateway.types import ImagePart, LLMRequest, ToolCall, count_images, text_of
from auoreo.harness.manifests import Rule
from auoreo.harness.orchestrator import build_spec
from auoreo.runtime.multimodal import MAX_IMAGES_PER_REQUEST, prepare_messages, tool_messages
from auoreo.tools import images
from auoreo.tools.context import ToolContext, ToolError, ToolResult
from tests import media
from tests.conftest import write

ALLOW = [Rule(permission="*", pattern="*", action="allow")]


@pytest.fixture
def settings(home: Path) -> Settings:
    return Settings(home=home, gateway="fake", approval_timeout_s=5, backends={"capture": "fake"})


@pytest.fixture
async def services(settings: Settings, workdir: Path, gateway: FakeGateway) -> AsyncIterator[Services]:
    # project rule so the `ask`-by-default screenshot runs in non-interactive turns
    write(
        workdir / ".auoreo" / "permissions" / "screen.yaml",
        "rules:\n  - {permission: screen, pattern: '*', action: allow}\n",
    )
    svc = await Services.create(settings, cwd=workdir, gateway=gateway, with_mcp=False)
    try:
        yield svc
    finally:
        await svc.close()


def ctx_for(svc: Services, workdir: Path, model: str = "default") -> ToolContext:
    spec = build_spec(svc.harness, "default", model, "manual")
    engine = PermissionEngine(list(ALLOW), plans_dir=str(workdir / ".auoreo" / "plans"))
    return ToolContext(
        session_id="s1",
        cwd=workdir,
        home=svc.settings.home,
        harness=svc.harness,
        services={"memory": svc.memory, "services": svc, "engine": engine, "spec": spec},
    )


async def call(svc: Services, ctx: ToolContext, name: str, args: dict) -> ToolResult:
    out = await svc.coordinator.runner.executor.run(
        ToolCall("c1", name, json.dumps(args)),
        tools=svc.harness.tools,
        ctx=ctx,
        engine=ctx.services["engine"],
    )
    return out.result


def vision_requests(gw: FakeGateway) -> list[LLMRequest]:
    return [r for r in gw.requests if r.metadata.get("purpose") == "vision"]


def decode(part: ImagePart) -> Image.Image:
    return Image.open(io.BytesIO(base64.b64decode(part.data_b64)))


# ------------------------------------------------------------------ images.py
def test_sniff_formats_and_svg_rejection() -> None:
    assert images.sniff(media.png()) == "png"
    assert images.sniff(media.gradient_jpeg(10, 10)) == "jpeg"
    buf = io.BytesIO()
    Image.new("RGB", (4, 4)).save(buf, "GIF")
    assert images.sniff(buf.getvalue()) == "gif"
    buf = io.BytesIO()
    Image.new("RGB", (4, 4)).save(buf, "WEBP")
    assert images.sniff(buf.getvalue()) == "webp"
    with pytest.raises(ToolError, match="SVG"):
        images.sniff(media.SVG)
    with pytest.raises(ToolError, match="SVG"):
        images.sniff(b"  <svg xmlns='http://www.w3.org/2000/svg'/>")
    with pytest.raises(ToolError, match="not a supported image"):
        images.sniff(b"GIF-ish but no")


def test_normalize_ladder_caps_edge_and_bytes() -> None:
    out, mt, info = images.normalize(media.gradient_jpeg(4000, 2000))
    assert mt == "image/jpeg" and info["orig_width"] == 4000
    assert (info["width"], info["height"]) == (1568, 784)
    assert len(out) <= images.TARGET_BYTES and info["quality"] == 85

    # incompressible noise: quality ladder exhausted → downscaled until ≤ 256 KB
    out, mt, info = images.normalize(media.noise_png(2000, 1500))
    assert mt == "image/jpeg" and len(out) <= images.TARGET_BYTES
    assert max(info["width"], info["height"]) < 1568 and info["quality"] == 50


def test_normalize_keeps_small_transparent_png_and_flattens_opaque() -> None:
    out, mt, _ = images.normalize(media.png(32, 32, (0, 0, 255, 128), mode="RGBA"))
    assert mt == "image/png" and Image.open(io.BytesIO(out)).mode == "RGBA"
    _, mt, _ = images.normalize(media.png(32, 32, (0, 0, 255, 255), mode="RGBA"))  # alpha unused
    assert mt == "image/jpeg"


def test_normalize_applies_exif_orientation_and_strips_metadata() -> None:
    src = media.exif_jpeg(200, 100, orientation=6)
    assert Image.open(io.BytesIO(src)).getexif().get(0x010F) == "SpyCam"
    out, _, info = images.normalize(src)
    img = Image.open(io.BytesIO(out))
    assert img.size == (100, 200) and (info["width"], info["height"]) == (100, 200)
    assert len(img.getexif()) == 0 and b"SpyCam" not in out


def test_normalize_hard_ceiling() -> None:
    with pytest.raises(ToolError, match="20 MB"):
        images.normalize(b"\x89PNG\r\n\x1a\n" + b"\0" * (21 * 1024 * 1024))


# ------------------------------------------------------------- capture backend
async def test_fake_capture_backend_and_mss_without_display(
    services: Services, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = services.backend("capture")
    assert getattr(fake, "is_test_backend", False)
    assert (await fake.check()).ok
    img = Image.open(io.BytesIO(await fake.grab()))
    assert img.format == "PNG" and img.size == (320, 200)
    assert Image.open(io.BytesIO(await fake.grab(region=(10, 10, 50, 40)))).size == (50, 40)

    mss_cls = services.harness.select_backend("capture", None)
    assert mss_cls is not None and mss_cls.manifest.name == "mss" and mss_cls.manifest.default
    backend = mss_cls.load()(services.settings)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    status = await backend.check()
    assert not status.ok and status.detail.startswith("no display") and "fake" in status.remedy
    monkeypatch.setenv("DISPLAY", ":97")  # set but unreachable: must still not raise
    status = await backend.check()
    assert not status.ok and "no display" in status.detail
    monkeypatch.delenv("DISPLAY")
    with pytest.raises(ToolError, match="no display"):
        await backend.grab()


# ------------------------------------------------------------------- tools
async def test_screenshot_tool_returns_image_part(services: Services, workdir: Path) -> None:
    res = await call(services, ctx_for(services, workdir), "screenshot", {})
    assert not res.is_error, res.content
    assert "Captured monitor 1: 320x200" in res.content
    assert len(res.images) == 1 and res.images[0].media_type == "image/jpeg"
    assert decode(res.images[0]).size == (320, 200)
    assert res.images[0].part()["image_url"]["url"].startswith("data:image/jpeg;base64,")


async def test_screenshot_asks_by_default(home: Path, workdir: Path, gateway: FakeGateway) -> None:
    svc = await Services.create(
        Settings(home=home, gateway="fake", backends={"capture": "fake"}),
        cwd=workdir,
        gateway=gateway,
        with_mcp=False,
    )
    try:
        ctx = ctx_for(svc, workdir)
        ctx.services["engine"] = PermissionEngine([])
        res = await call(svc, ctx, "screenshot", {})
        assert res.is_error and "needs approval" in res.content
    finally:
        await svc.close()


async def test_image_info(services: Services, workdir: Path) -> None:
    (workdir / "a.png").write_bytes(media.png(64, 48))
    (workdir / "x.svg").write_bytes(media.SVG)
    ctx = ctx_for(services, workdir)
    res = await call(services, ctx, "image_info", {"path": "a.png"})
    assert not res.is_error and "PNG (image/png), 64x48 px" in res.content
    res = await call(services, ctx, "image_info", {"path": "x.svg"})
    assert res.is_error and "SVG images are not supported" in res.content
    (workdir / ".env").write_bytes(media.png())
    res = await call(services, ctx, "image_info", {"path": ".env"})
    assert res.is_error and "permission denied" in res.content  # secret floor applies


async def test_vision_understand_with_vision_model(
    services: Services, gateway: FakeGateway, workdir: Path
) -> None:
    (workdir / "big.jpg").write_bytes(media.gradient_jpeg(4000, 2000))
    res = await call(
        services,
        ctx_for(services, workdir),
        "vision_understand",
        {"path": "big.jpg", "question": "What is it?"},
    )
    assert not res.is_error, res.content
    assert res.content == "vision: What is it? [+1 images]" and res.meta["model"] == "default"
    (req,) = vision_requests(gateway)
    assert req.model == "default" and req.tool_choice == "none"
    user = req.messages[-1]["content"]
    assert user[0] == {"type": "text", "text": "What is it?"}
    url = user[1]["image_url"]["url"]
    assert user[1]["type"] == "image_url" and url.startswith("data:image/jpeg;base64,")
    sent = Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1])))
    assert max(sent.size) == 1568


async def test_vision_understand_falls_back_to_vision_group_or_errors(
    home: Path, workdir: Path, gateway: FakeGateway
) -> None:
    (workdir / "a.png").write_bytes(media.png())
    gateway.vision = False
    svc = await Services.create(
        Settings(home=home, gateway="fake"), cwd=workdir, gateway=gateway, with_mcp=False
    )
    try:
        res = await call(svc, ctx_for(svc, workdir), "vision_understand", {"path": "a.png", "question": "q"})
        assert res.is_error and "no vision-capable model" in res.content
        assert not vision_requests(gateway)
        svc.settings.models["vision"] = ModelGroup(models=["openai/gpt-4o"])
        res = await call(svc, ctx_for(svc, workdir), "vision_understand", {"path": "a.png", "question": "q"})
        assert not res.is_error and res.meta["model"] == "vision"
        assert vision_requests(gateway)[-1].model == "vision"
    finally:
        await svc.close()


async def test_vision_understand_needs_exactly_one_source(services: Services, workdir: Path) -> None:
    res = await call(services, ctx_for(services, workdir), "vision_understand", {"question": "q"})
    assert res.is_error and "exactly one" in res.content
    res = await call(
        services,
        ctx_for(services, workdir),
        "vision_understand",
        {"url": "file:///etc/passwd", "question": "q"},
    )
    assert res.is_error and "only http/https" in res.content


async def test_ocr_falls_back_to_vision(services: Services, gateway: FakeGateway, workdir: Path) -> None:
    (workdir / "scan.png").write_bytes(media.png(200, 80, (255, 255, 255)))
    res = await call(services, ctx_for(services, workdir), "ocr", {"path": "scan.png"})
    assert not res.is_error, res.content
    assert res.content.startswith("[ocr engine: vision fallback via default (tesseract not available)]")
    assert res.meta["engine"] == "vision"
    (req,) = vision_requests(gateway)
    assert text_of(req.messages[-1]["content"]) == "Transcribe all text in this image verbatim."
    assert count_images(req.messages[-1]["content"]) == 1


# --------------------------------------------------------- gateway + runner
async def test_fake_echo_counts_images(gateway: FakeGateway) -> None:
    content = [{"type": "text", "text": "look"}, ImagePart("image/png", "AAAA").part()]
    req = LLMRequest(model="m", messages=({"role": "user", "content": content},))
    text = "".join([getattr(i, "text", "") async for i in gateway.stream(req)])
    assert text == "echo: look [+1 images]"
    assert gateway.supports_vision("m")
    huge = json.dumps([ImagePart("image/png", "A" * 400_000).part()])
    assert gateway.count_tokens("m", huge) < 2000  # images are not charged by base64 length


def test_tool_messages_and_request_cap() -> None:
    img = ImagePart("image/png", "AAAA", "shot")
    res = ToolResult("done", images=[img, img])
    msg, parts = tool_messages("c9", res, vision=True)
    assert msg["role"] == "tool" and "[2 images attached in the next message]" in msg["content"]
    assert parts[0] == {"type": "text", "text": '<tool-image call_id="c9">shot</tool-image>'}
    assert count_images(parts) == 2
    msg, parts = tool_messages("c9", res, vision=False)
    assert parts == [] and msg["content"] == "done\n[image omitted: shot]\n[image omitted: shot]"

    msgs = [{"role": "user", "content": [img.part()]} for _ in range(MAX_IMAGES_PER_REQUEST + 3)]
    capped = prepare_messages(msgs, vision=True)
    assert sum(count_images(m["content"]) for m in capped) == MAX_IMAGES_PER_REQUEST
    assert all(count_images(m["content"]) == 0 for m in capped[:3])  # oldest stripped
    assert "image cap" in text_of(capped[0]["content"])
    few = msgs[:5]
    assert prepare_messages(few, vision=True) is few  # under the cap: untouched
    blind = prepare_messages(msgs[:2], vision=False)
    assert sum(count_images(m["content"]) for m in blind) == 0
    assert "[image omitted" in text_of(blind[0]["content"])


async def _run_turn(svc: Services, text: str) -> list[dict]:
    sess = await svc.create_session()
    await svc.coordinator.submit(sess.id, text, interactive=False)
    await svc.coordinator.wait(sess.id, 10)
    return await svc.coordinator.runner.load_history(sess.id)


async def test_runner_sends_tool_images_as_user_message(services: Services, gateway: FakeGateway) -> None:
    gateway.queue(Scripted(tool_calls=[("screenshot", {}), ("screenshot", {"monitor": 0})]))
    hist = await _run_turn(services, "look at my screen")
    assert [m["role"] for m in hist] == ["user", "assistant", "tool", "tool", "user", "assistant"]
    img_msg = hist[4]["content"]
    assert count_images(img_msg) == 2
    assert img_msg[0]["text"].startswith('<tool-image call_id="call_1">screenshot of monitor 1')
    assert img_msg[2]["text"].startswith('<tool-image call_id="call_2">screenshot of monitor 0')
    # the request after the tool round carried the tool messages first, then the image message
    last = [r for r in gateway.requests if not r.metadata.get("purpose")][-1]
    roles = [m["role"] for m in last.messages]
    assert roles[-3:] == ["tool", "tool", "user"] and count_images(last.messages[-1]["content"]) == 2
    assert hist[-1]["content"].endswith("[+2 images]")


async def test_runner_uses_placeholder_without_vision(services: Services, gateway: FakeGateway) -> None:
    gateway.vision = False
    gateway.queue(Scripted(tool_calls=[("screenshot", {})]), Scripted(text="ok"))
    hist = await _run_turn(services, "look")
    assert [m["role"] for m in hist] == ["user", "assistant", "tool", "assistant"]
    assert "[image omitted: screenshot of monitor 1 (320x200 image/jpeg)]" in hist[2]["content"]
    for req in gateway.requests:
        assert all(count_images(m.get("content")) == 0 for m in req.messages)
