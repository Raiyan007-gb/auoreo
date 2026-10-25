"""F4 tone classifier → `{{tone}}` in the Tier-2 context (never the stable system prompt)."""

from __future__ import annotations

from pathlib import Path

import pytest

from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.gateway.fake import FakeGateway
from auoreo.harness.orchestrator import build_spec, render_context
from auoreo.voice.audio import wav_from_pcm
from auoreo.voice.tone import GUIDANCE, Prosody, classify, detect_language, prosody_of, tone_of
from tests.audio import tone_pcm


@pytest.mark.parametrize(
    ("text", "label"),
    [
        ("hello, can you list the files in src", "neutral"),
        ("what is the time?", "neutral"),
        ("this is broken AGAIN, nothing works!!", "frustrated"),
        ("WHY IS THIS HAPPENING", "frustrated"),
        ("urgent: production is down, fix it asap", "urgent"),
        ("I don't understand, what do you mean??", "confused"),
        ("thanks, that works now, great!", "positive"),
        ("Das funktioniert schon wieder nicht!!", "frustrated"),  # de
        ("je ne comprends pas, ce n'est pas clair", "confused"),  # fr
        ("gracias, perfecto 👍", "positive"),  # es
        ("è urgente, serve subito", "urgent"),  # it
        ("これは動かない。イライラする", "frustrated"),  # ja
        ("紧急！马上处理", "urgent"),  # zh
    ],
)
def test_classifier_table(text: str, label: str) -> None:
    t = classify(text)
    assert t.label == label, (text, t)
    assert 0.0 <= t.confidence <= 1.0
    assert classify(text) == t  # deterministic


def test_confidence_grows_with_evidence() -> None:
    weak, strong = classify("this is annoying"), classify("this is annoying and broken, nothing works!!!")
    assert weak.label == strong.label == "frustrated"
    assert strong.confidence > weak.confidence and strong.confidence >= 0.7


def test_prosody_features() -> None:
    loud_quiet = wav_from_pcm(tone_pcm(0.5, amp=0.9) + tone_pcm(0.5, amp=0.05))
    p = prosody_of(loud_quiet, [{"start": 0.0, "end": 1.0, "text": "one two three four five"}])
    assert p.rms_var is not None and p.rms_var > 0.01 and p.words_per_s == 5.0
    calm = classify("please hurry")
    agitated = classify("please hurry", Prosody(rms_var=0.05, words_per_s=4.5))
    assert agitated.label == "urgent" and agitated.confidence > calm.confidence


def test_detect_language() -> None:
    assert detect_language("what is the status of this build") == "en"
    assert detect_language("ich kann das nicht finden, bitte hilf mir") == "de"
    assert detect_language("こんにちは、元気ですか") == "ja"
    assert detect_language("ok") is None


def test_tone_lines(home: Path) -> None:
    st = Settings(home=home, gateway="fake")
    assert tone_of("list the files", st) == "tone: neutral (0.90)"
    angry = "this is broken again, nothing works!!"
    assert "guidance" not in tone_of(angry, st)  # adaptive is opt-in
    st.tone_adaptive = True
    assert tone_of(angry, st).splitlines()[1] == f"guidance: {GUIDANCE}"
    assert "guidance" not in tone_of("thanks, great work!", st)  # only frustrated/confused
    assert "guidance" not in tone_of("this is annoying", st)  # confidence < 0.7
    st.tone_enabled = False
    assert tone_of(angry, st) == ""
    st.locale = "de-DE"
    assert tone_of("what is the status of this build", st) == (
        "language: the user wrote in en; answer in de-DE (the configured locale)"
    )
    assert tone_of("ich kann das nicht finden, bitte hilf mir", st) == ""


def test_tone_rendered_in_context_only(services: Services) -> None:
    spec = build_spec(services.harness, "default", "default", "manual")
    assert "{{tone}}" in spec.context_template and "{{tone}}" not in spec.system_prompt
    ctx = render_context(spec, Path("."), "manual", tone="tone: urgent (0.85)")
    assert "tone: urgent (0.85)" in ctx and ctx.index("tone:") < ctx.index("</runtime>")
    assert "tone:" not in render_context(spec, Path("."), "manual")


async def test_stable_prompt_identical_across_tones(home: Path, workdir: Path) -> None:
    gateway = FakeGateway()
    svc = await Services.create(
        Settings(home=home, gateway="fake", tone_adaptive=True), cwd=workdir, gateway=gateway, with_mcp=False
    )
    try:
        sess = await svc.create_session()
        for text in ("hello there", "this is broken again, nothing works!!", "thanks, perfect!"):
            await svc.coordinator.submit(sess.id, text, interactive=False)
            await svc.coordinator.wait(sess.id, 10)
        main = [r for r in gateway.requests if r.metadata.get("purpose") not in {"title", "compaction"}]
        assert len(main) == 3
        systems = {str(r.messages[0]["content"]) for r in main}
        assert len(systems) == 1 and "tone:" not in systems.pop()
        users = [str(m.get("content")) for r in main for m in r.messages if m.get("role") == "user"]
        assert any("tone: neutral" in u for u in users)
        assert any("tone: frustrated" in u and f"guidance: {GUIDANCE}" in u for u in users)
        assert any("tone: positive" in u for u in users)
    finally:
        await svc.close()
