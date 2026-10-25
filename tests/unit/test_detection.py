"""W2-C: detection backends (fake blobs, RF-DETR licence gate, Grounding-DINO, YOLO sidecar
contract), the annotated-image helper and the `detect_objects` tool."""

from __future__ import annotations

import ast
import base64
import io
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from PIL import Image

from auoreo.backends.detection import Box
from auoreo.core.diagnostics import backend_checks
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.gateway.fake import FakeGateway, Scripted
from auoreo.gateway.types import count_images
from auoreo.harness.registry import Harness
from auoreo.perception.draw import annotate
from auoreo.perception.models import ModelRefused, rfdetr_size
from tests.conftest import REPO
from tests.perception_helpers import CRATE, PERSON, call, ctx_for, scene_png, write_png


def backend(h: Harness, kind: str, name: str, settings: Settings) -> Any:
    return h.backends_for(kind)[name].load()(settings)


async def test_fake_detector_boxes_labels_and_threshold(services: Services) -> None:
    det = services.backend("detection")
    assert det.name == "fake" and det.open_vocabulary is False
    boxes = await det.detect(scene_png())
    assert [(b.label, b.score) for b in boxes][:2] == [("object", 1.0), ("person", 1.0)]
    assert len(boxes) == 3 and boxes[2].label == "object" and 0.7 < boxes[2].score < 0.9
    person = next(b for b in boxes if b.label == "person")
    assert (person.x0, person.y0) == (PERSON[0], PERSON[1])
    assert abs(person.x1 - (PERSON[2] + 1)) <= 3 and abs(person.y1 - (PERSON[3] + 1)) <= 3
    assert len(await det.detect(scene_png(), threshold=0.9)) == 2  # the disc drops out
    only = await det.detect(scene_png(), classes=["person"])
    assert [b.label for b in only] == ["person"]
    assert await det.detect(scene_png(), classes=["giraffe"]) == []
    assert await det.detect(scene_png()) == boxes  # deterministic


def test_annotate_draws_boxes() -> None:
    src = scene_png()
    out = annotate(src, [Box("object", 1.0, *CRATE)])
    img = Image.open(io.BytesIO(out)).convert("RGB")
    orig = Image.open(io.BytesIO(src)).convert("RGB")
    assert img.size == orig.size
    # the crate's outline is drawn in the palette's first colour where the image was plain blue
    assert img.getpixel((CRATE[0] + 40, CRATE[3])) != orig.getpixel((CRATE[0] + 40, CRATE[3]))
    assert img.getpixel((5, 230)) == orig.getpixel((5, 230))  # untouched elsewhere


@pytest.mark.parametrize("size", ["xlarge", "2xlarge", "XL", "rf-detr-2xlarge"])
def test_rfdetr_rejects_pml_sizes(size: str) -> None:
    with pytest.raises(ModelRefused, match="PML-1.0"):
        rfdetr_size(Settings(detection_model=size))


async def test_rfdetr_check_is_honest(services: Services, home: Path) -> None:
    assert rfdetr_size(Settings(detection_model="small"))[1] == "RFDETRSmall"
    banned = backend(services.harness, "detection", "rfdetr", Settings(home=home, detection_model="xlarge"))
    st = await banned.check()
    assert not st.ok and "PML-1.0" in st.detail and "nano" in st.remedy
    unknown = backend(services.harness, "detection", "rfdetr", Settings(home=home, detection_model="huge"))
    assert "unknown" in (await unknown.check()).detail
    nano = backend(services.harness, "detection", "rfdetr", services.settings)
    st = await nano.check()
    assert not st.ok and "rfdetr" in st.detail and "pip install rfdetr" in st.remedy  # not installed here


async def test_grounding_dino_check_is_honest(services: Services) -> None:
    gd = backend(services.harness, "detection", "grounding_dino", services.settings)
    assert gd.open_vocabulary is True
    st = await gd.check()
    assert not st.ok and "not installed" in st.detail and "transformers" in st.remedy


async def test_yolo_sidecar_contract(services: Services, home: Path) -> None:
    unset = backend(services.harness, "detection", "yolo_onnx", services.settings)
    st = await unset.check()
    assert not st.ok and "yolo_sidecar_url is not set" in st.detail
    seen: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/health":
            return httpx.Response(200, json={"ok": True, "model": "yolo26n", "license": "AGPL-3.0"})
        body = json.loads(req.content)
        seen.append(body)
        assert base64.b64decode(body["image_b64"]).startswith(b"\x89PNG")
        return httpx.Response(
            200, json={"boxes": [{"label": "person", "score": 0.91, "x0": 1, "y0": 2, "x1": 30, "y1": 90}]}
        )

    yolo = backend(
        services.harness,
        "detection",
        "yolo_onnx",
        Settings(home=home, yolo_sidecar_url="http://127.0.0.1:8765/"),
    )
    yolo.transport = httpx.MockTransport(handler)
    st = await yolo.check()
    assert st.ok and "yolo26n" in st.detail and "isolated" in st.detail
    boxes = await yolo.detect(scene_png(), classes=["person"], threshold=0.5)
    assert boxes == [Box("person", 0.91, 1.0, 2.0, 30.0, 90.0)]
    assert seen[0]["classes"] == ["person"] and seen[0]["threshold"] == 0.5
    yolo.transport = httpx.MockTransport(lambda r: httpx.Response(500))
    assert not (await yolo.check()).ok


def test_ultralytics_is_never_imported() -> None:
    roots = [REPO / "src" / "auoreo", REPO / "backends", REPO / "tools"]
    for path in (p for r in roots for p in r.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else []
            if isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            assert not any(n.split(".")[0] in {"ultralytics", "face_recognition", "dlib"} for n in names), (
                path
            )


async def test_detect_objects_tool(services: Services, workdir: Path) -> None:
    write_png(workdir, "scene.png", scene_png())
    res = await call(services, ctx_for(services, workdir), "detect_objects", {"image": "scene.png"})
    assert not res.is_error, res.content
    assert res.content.startswith("[detection: fake, fixed labels, threshold 0.3] 3 box(es)")
    assert "- person 1.00 [20, 20," in res.content
    assert res.meta["count"] == 3 and res.meta["boxes"][1]["label"] == "person"
    assert len(res.images) == 1 and "3 detection box(es)" in res.images[0].note
    res = await call(
        services,
        ctx_for(services, workdir),
        "detect_objects",
        {"image": "scene.png", "threshold": 0.95, "annotate": False},
    )
    assert "2 box(es)" in res.content and res.images == []
    res = await call(services, ctx_for(services, workdir), "detect_objects", {})
    assert res.is_error and "exactly one of" in res.content


async def test_detect_objects_screen_source(home: Path, workdir: Path, gateway: FakeGateway) -> None:
    svc = await Services.create(
        Settings(home=home, gateway="fake", backends={"capture": "fake"}),
        cwd=workdir,
        gateway=gateway,
        with_mcp=False,
    )
    try:
        res = await call(svc, ctx_for(svc, workdir), "detect_objects", {"source": "screen"})
        assert not res.is_error and "in screen capture" in res.content
    finally:
        await svc.close()


async def test_detect_objects_through_runner_after_tool_search(
    services: Services, gateway: FakeGateway, workdir: Path
) -> None:
    write_png(workdir, "scene.png", scene_png())
    gateway.queue(
        Scripted(tool_calls=[("tool_search", {"query": "detect objects bounding boxes", "limit": 3})]),
        Scripted(tool_calls=[("detect_objects", {"image": "scene.png"})]),
        Scripted(text="three things"),
    )
    sess = await services.create_session()
    await services.coordinator.submit(sess.id, "what is in scene.png?", interactive=False)
    await services.coordinator.wait(sess.id, 10)
    hist = await services.coordinator.runner.load_history(sess.id)
    tool_msgs = [m["content"] for m in hist if m["role"] == "tool"]
    assert "detect_objects" in tool_msgs[0]
    assert "3 box(es)" in tool_msgs[1] and "- person 1.00" in tool_msgs[1]
    main = [r for r in gateway.requests if not r.metadata.get("purpose")]
    assert "detect_objects" not in {t["function"]["name"] for t in main[0].tools}  # deferred
    assert count_images(main[-1].messages[-1]["content"]) == 1  # the annotated image


async def test_doctor_rows_for_perception(services: Services) -> None:
    rows = {c.check: c for c in await backend_checks(services.settings, services.harness, services)}
    assert rows["backend:detection"].level == "ok" and "fake colour-blob" in rows["backend:detection"].detail
    assert rows["backend:face"].level == "ok"
    assert rows["backend:sensor"].level == "ok"
