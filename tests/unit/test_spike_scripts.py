"""F7 spike scripts stay runnable: Nuitka command composition and the RL-vision experiment."""

from __future__ import annotations

import argparse
import importlib.util
import random
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]


def load(rel: str, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(mod)
    return mod


nk = load("scripts/build_binary_nuitka.py", "build_binary_nuitka_under_test")
rv = load("scripts/spikes/rl_vision.py", "rl_vision_under_test")


def test_nuitka_command_mirrors_the_pyinstaller_inputs() -> None:
    cmd = nk.command(argparse.Namespace(standalone=False, jobs=2, dist=ROOT / "dist" / "nuitka"))
    assert cmd[1:4] == ["-m", "nuitka", "--mode=onefile"]
    joined = " ".join(cmd)
    # definitions land where bundled_root() looks when not frozen by PyInstaller
    assert f"--include-data-dir={ROOT / 'tools'}=auoreo/_bundled/tools" in cmd
    assert f"--include-data-files={ROOT / 'tools'}=auoreo/_bundled/tools/=**/*.py" in cmd
    assert "=auoreo/core/alembic/=**/*.py" in joined and "=auoreo/plugins/rt/=**/*.py" in joined
    assert "--include-package=litellm" in cmd and "--nofollow-import-to=litellm.proxy" in cmd
    assert "--nofollow-import-to=torch" in cmd
    assert f"--user-package-configuration-file={nk.PACKAGE_CONFIG}" in cmd and nk.PACKAGE_CONFIG.is_file()
    assert cmd[-1].endswith("packaging/pyinstaller/entry.py")


def test_rl_vision_matching_and_outcome() -> None:
    gt = [("person", 0.0, 0.0, 10.0, 30.0)]
    assert rv.match([("person", 1.0, 0.0, 11.0, 30.0)], gt) == (1, 0, 0)
    assert rv.match([("object", 1.0, 0.0, 11.0, 30.0)], gt) == (0, 1, 1)  # label-aware
    dets = {"full": [[(gt[0], 0.9)]] * rv.FRAMES, "lite": [[]] * rv.FRAMES}
    truth = [gt] * rv.FRAMES
    every = rv.outcome(dets, truth, rv.Action("full", 0.3, 1))
    sparse = rv.outcome(dets, truth, rv.Action("full", 0.3, 3))
    assert every["f1"] == sparse["f1"] == 1.0 and sparse["cost"] < every["cost"]
    assert rv.outcome(dets, truth, rv.Action("full", 0.95, 1))["f1"] == 0.0


async def test_rl_vision_table_uses_the_fake_detector_end_to_end() -> None:
    rows, timing = await rv.build_table(1, seed=3)
    assert len(rows) == len(rv.CONTEXTS) and set(timing) == {"full", "lite"}
    assert set(rows[0]["out"]) == {a.key for a in rv.ACTIONS}
    pool = {c: [r for r in rows if r["ctx"] == c] for c in rv.CONTEXTS}
    curve, stats = rv.run_bandit(pool, 50, random.Random(0), contextual=True)
    assert len(curve) == 50 and stats
