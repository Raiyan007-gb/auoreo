"""F7 spike: a contextual bandit choosing a perception action (detector, threshold, frame
interval) from simulated sensor contexts. Offline; see docs/spikes/rl-vision.md.

    uv run python scripts/spikes/rl_vision.py [--seeds 10] [--rounds 1500] [--scenes 24]

Scenes are synthetic 160x120 clips (6 frames) drawn with Pillow, detected by the real `fake`
detection backend (`backends/detection/fake`, the colour-blob detector):

- context = lighting {bright, dim} x clutter {low, high} x motion {static, fast};
- targets are solid rectangles (tall = `person`, else `object`); dim light lowers their
  contrast, so their score drops; clutter adds partially filled shapes (rings, discs,
  diagonals) that the detector also reports and that are *not* ground truth;
- fast motion moves targets ~10 px/frame, so a detection held over skipped frames goes stale.

Action = backend {full, lite} x threshold {0.3 .. 0.9} x interval {1, 3} (20 arms). `lite` is
the same detector on a 0.4x downscaled frame (cheaper, misses small targets). Reward per
clip = F1 (IoU >= 0.5, label-aware, micro over frames) - LAMBDA * cost, where cost is the
fraction of a full-detector-every-frame budget (full = 1.0, lite = 0.35 per call).

Every (clip, arm) outcome is precomputed once (the detector is deterministic), then the
policies replay over it with several seeds: `auoreo.policy.bandit.choose` (epsilon-greedy
with decay, per context and global), fixed baselines and per-context hindsight oracles.
Evaluation of the learned greedy policy uses held-out clips.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import io
import json
import random
import statistics
import sys
import time
from dataclasses import dataclass
from itertools import product
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from PIL import Image, ImageDraw  # noqa: E402

from auoreo.policy.bandit import choose  # noqa: E402
from auoreo.policy.config import Arm, BanditSettings  # noqa: E402

W, H, FRAMES = 160, 120, 6
LIGHTING, CLUTTER, MOTION = ("bright", "dim"), ("low", "high"), ("static", "fast")
CONTEXTS = [f"{a}|{b}|{c}" for a, b, c in product(LIGHTING, CLUTTER, MOTION)]
BACKENDS = {"full": (1.0, 1.0), "lite": (0.4, 0.35)}  # name -> (frame scale, cost per call)
THRESHOLDS = (0.3, 0.45, 0.6, 0.75, 0.9)
INTERVALS = (1, 3)
LAMBDA = 0.3
IOU_MIN = 0.5
DEFAULT_ARM = "full/t0.3/k1"  # what detect_objects does today (threshold 0.3, every frame)
WINDOWS = ((1, 100), (101, 250), (251, 500), (501, 1000), (1001, 1500), (1501, 3000))

Box = tuple[str, float, float, float, float]  # label, x0, y0, x1, y1


@dataclass(frozen=True)
class Action:
    backend: str
    threshold: float
    interval: int

    @property
    def key(self) -> str:
        return f"{self.backend}/t{self.threshold:g}/k{self.interval}"


ACTIONS = [Action(b, t, k) for b in BACKENDS for t in THRESHOLDS for k in INTERVALS]
ARMS = [Arm(model=a.key) for a in ACTIONS]


# ------------------------------------------------------------------ scenes
def _color(rng: random.Random, bg: int, dist: tuple[int, int]) -> tuple[int, int, int]:
    """A colour whose distance from the grey background lies roughly in `dist`."""
    d = rng.uniform(*dist) / (3**0.5)
    sign = rng.choice((-1, 1))
    return tuple(max(0, min(255, int(bg + sign * d + rng.uniform(-4, 4)))) for _ in range(3))  # type: ignore[return-value]


def make_clip(ctx: str, rng: random.Random) -> tuple[list[bytes], list[list[Box]]]:
    """Frames (PNG) + ground truth per frame for one context."""
    lighting, clutter, motion = ctx.split("|")
    bg = 120 if lighting == "bright" else 60
    dist = (170, 220) if lighting == "bright" else (62, 95)
    targets = []
    for _ in range(rng.randint(1, 3)):
        person = rng.random() < 0.5  # aspect >= 1.875 vs <= 1.57: the detector's 1.6 cut is unambiguous
        w, h = (
            (rng.randint(10, 16), rng.randint(30, 40))
            if person
            else (rng.randint(14, 30), rng.randint(10, 22))
        )
        for _try in range(30):  # targets start apart (they may cross while moving)
            x, y = rng.uniform(4, W - w - 4), rng.uniform(4, H - h - 4)
            if all(
                x + w + 4 < t[1] or t[1] + t[3] + 4 < x or y + h + 4 < t[2] or t[2] + t[4] + 4 < y
                for t in targets
            ):
                break
        speed = 10.0 if motion == "fast" else 0.0
        vx = rng.choice((-1, 1)) * speed
        targets.append(["person" if person else "object", x, y, w, h, vx, _color(rng, bg, dist)])
    distractors = []
    for _ in range(rng.randint(3, 5) if clutter == "high" else rng.randint(0, 1)):
        kind = rng.choice(("ring", "disc", "diag"))
        s = rng.randint(14, 26)
        x, y = rng.uniform(2, W - s - 2), rng.uniform(2, H - s - 2)
        distractors.append((kind, x, y, s, _color(rng, bg, (dist[0] * 0.9, dist[1] * 1.1))))
    frames, truth = [], []
    for f in range(FRAMES):
        img = Image.new("RGB", (W, H), (bg, bg, bg))
        d = ImageDraw.Draw(img)
        for kind, x, y, s, col in distractors:
            box = (x, y, x + s, y + s)
            if kind == "ring":
                d.ellipse(box, outline=col, width=3)
            elif kind == "disc":
                d.ellipse(box, fill=col)
            else:
                d.line((x, y, x + s, y + s), fill=col, width=4)
        gt: list[Box] = []
        for label, x, y, w, h, vx, col in targets:
            x0 = x + vx * f
            if x0 < 0 or x0 + w > W:  # bounce
                x0 = max(0.0, min(W - w, x0 % (2 * (W - w))))
            d.rectangle((x0, y, x0 + w - 1, y + h - 1), fill=col)
            gt.append((label, x0, y, x0 + w, y + h))
        buf = io.BytesIO()
        img.save(buf, "PNG")
        frames.append(buf.getvalue())
        truth.append(gt)
    return frames, truth


# --------------------------------------------------------------- detection
def load_fake_backend() -> Any:
    path = ROOT / "backends" / "detection" / "fake" / "impl.py"
    spec = importlib.util.spec_from_file_location("fake_detection_impl", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.Backend(None)


def _scaled(png: bytes, scale: float) -> bytes:
    if scale >= 1.0:
        return png
    img = Image.open(io.BytesIO(png))
    small = img.resize((max(1, int(W * scale)), max(1, int(H * scale))), Image.Resampling.BILINEAR)
    buf = io.BytesIO()
    small.save(buf, "PNG")
    return buf.getvalue()


async def detect_all(backend: Any, png: bytes, name: str) -> tuple[list[tuple[Box, float]], float]:
    """All blobs (threshold 0) in full-frame coordinates + wall time in ms."""
    scale = BACKENDS[name][0]
    t0 = time.perf_counter()
    boxes = await backend.detect(_scaled(png, scale), threshold=0.0)
    ms = (time.perf_counter() - t0) * 1000
    out = [((b.label, b.x0 / scale, b.y0 / scale, b.x1 / scale, b.y1 / scale), b.score) for b in boxes]
    return out, ms


def iou(a: Box, b: Box) -> float:
    ix = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    iy = max(0.0, min(a[4], b[4]) - max(a[2], b[2]))
    inter = ix * iy
    union = (a[3] - a[1]) * (a[4] - a[2]) + (b[3] - b[1]) * (b[4] - b[2]) - inter
    return inter / union if union > 0 else 0.0


def match(pred: list[Box], gt: list[Box]) -> tuple[int, int, int]:
    """Greedy label-aware matching -> (tp, fp, fn)."""
    used: set[int] = set()
    tp = 0
    for p in pred:
        best, best_i = 0.0, -1
        for i, g in enumerate(gt):
            if i not in used and g[0] == p[0] and (v := iou(p, g)) > best:
                best, best_i = v, i
        if best >= IOU_MIN:
            used.add(best_i)
            tp += 1
    return tp, len(pred) - tp, len(gt) - tp


def outcome(
    dets: dict[str, list[list[tuple[Box, float]]]], truth: list[list[Box]], a: Action
) -> dict[str, float]:
    tp = fp = fn = calls = 0
    held: list[Box] = []
    for f in range(FRAMES):
        if f % a.interval == 0:
            held = [b for b, s in dets[a.backend][f] if s >= a.threshold]
            calls += 1
        t, p, n = match(held, truth[f])
        tp, fp, fn = tp + t, fp + p, fn + n
    f1 = 2 * tp / (2 * tp + fp + fn) if (tp + fp + fn) else 1.0
    cost = calls * BACKENDS[a.backend][1] / FRAMES
    return {"f1": f1, "cost": cost, "reward": f1 - LAMBDA * cost}


async def build_table(n_scenes: int, seed: int) -> tuple[list[dict[str, Any]], dict[str, float]]:
    """One row per clip: context + outcome of every action. Also detector timings."""
    rng = random.Random(seed)
    backend = load_fake_backend()
    rows: list[dict[str, Any]] = []
    timing: dict[str, list[float]] = {b: [] for b in BACKENDS}
    for ctx in CONTEXTS:
        for i in range(n_scenes):
            frames, truth = make_clip(ctx, rng)
            dets: dict[str, list[list[tuple[Box, float]]]] = {b: [] for b in BACKENDS}
            for name in BACKENDS:
                for png in frames:
                    boxes, ms = await detect_all(backend, png, name)
                    dets[name].append(boxes)
                    timing[name].append(ms)
            rows.append({"ctx": ctx, "i": i, "out": {a.key: outcome(dets, truth, a) for a in ACTIONS}})
    return rows, {b: round(statistics.median(v), 2) for b, v in timing.items()}


# ---------------------------------------------------------------- policies
def run_bandit(
    pool: dict[str, list[dict[str, Any]]], rounds: int, rng: random.Random, contextual: bool
) -> tuple[list[float], dict[str, dict[str, tuple[int, float]]]]:
    cfg = BanditSettings(enabled=True)  # shipped defaults: eps 0.15, -0.02 per 100 pulls, floor 0.02
    stats: dict[str, dict[str, tuple[int, float]]] = {}
    rewards = []
    for _ in range(rounds):
        ctx = rng.choice(CONTEXTS)
        row = rng.choice(pool[ctx])
        key = ctx if contextual else "*"
        s = stats.setdefault(key, {})
        arm = choose(cfg, ARMS, s, rng, key).arm
        r = row["out"][arm.model]["reward"]
        pulls, total = s.get(arm.key, (0, 0.0))
        s[arm.key] = (pulls + 1, total + r)
        rewards.append(r)
    return rewards, stats


def run_fixed(
    pool: dict[str, list[dict[str, Any]]], rounds: int, rng: random.Random, pick: Any
) -> list[float]:
    out = []
    for _ in range(rounds):
        ctx = rng.choice(CONTEXTS)
        row = rng.choice(pool[ctx])
        out.append(row["out"][pick(ctx)]["reward"])
    return out


def best_arm(rows: list[dict[str, Any]], metric: str = "reward") -> str:
    return max(ACTIONS, key=lambda a: statistics.fmean(r["out"][a.key][metric] for r in rows)).key


def greedy(stats: dict[str, dict[str, tuple[int, float]]], key: str) -> str:
    s = stats.get(key) or {}
    if not s:
        return DEFAULT_ARM
    return max(s, key=lambda k: s[k][1] / s[k][0]).split("|")[0]  # Arm.key = "<model>|-|-"


def evaluate(rows: list[dict[str, Any]], pick: Any) -> dict[str, float]:
    outs = [r["out"][pick(r["ctx"])] for r in rows]
    return {m: statistics.fmean(o[m] for o in outs) for m in ("f1", "cost", "reward")}


def window_means(curve: list[float]) -> list[float | None]:
    return [statistics.fmean(curve[a - 1 : b]) if len(curve) >= b else None for a, b in WINDOWS]


# ------------------------------------------------------------------ report
def fmt(mean: float, sd: float) -> str:
    return f"{mean:+.3f}±{sd:.3f}"


def report(args: argparse.Namespace, rows: list[dict[str, Any]], timing: dict[str, float]) -> dict[str, Any]:
    rng0 = random.Random(args.seed)
    by_ctx: dict[str, list[dict[str, Any]]] = {c: [] for c in CONTEXTS}
    for r in rows:
        by_ctx[r["ctx"]].append(r)
    train, test = {}, {}
    for c, rs in by_ctx.items():
        rs = rs[:]
        rng0.shuffle(rs)
        cut = len(rs) * 2 // 3
        train[c], test[c] = rs[:cut], rs[cut:]
    train_rows = [r for rs in train.values() for r in rs]
    test_rows = [r for rs in test.values() for r in rs]
    oracle = {c: best_arm(train[c]) for c in CONTEXTS}
    best_fixed = best_arm(train_rows)
    policies: dict[str, list[list[float]]] = {
        k: [] for k in ("contextual", "global", "default", "best_fixed", "oracle")
    }
    held: dict[str, list[dict[str, float]]] = {"contextual": [], "global": []}
    for s in range(args.seeds):
        rng = random.Random(args.seed * 1000 + s)
        curve, stats = run_bandit(train, args.rounds, rng, contextual=True)
        policies["contextual"].append(curve)
        held["contextual"].append(evaluate(test_rows, lambda c, st=stats: greedy(st, c)))
        curve, stats = run_bandit(train, args.rounds, rng, contextual=False)
        policies["global"].append(curve)
        held["global"].append(evaluate(test_rows, lambda c, st=stats: greedy(st, "*")))
        policies["default"].append(run_fixed(train, args.rounds, rng, lambda c: DEFAULT_ARM))
        policies["best_fixed"].append(run_fixed(train, args.rounds, rng, lambda c: best_fixed))
        policies["oracle"].append(run_fixed(train, args.rounds, rng, lambda c: oracle[c]))

    lines = [
        f"rl_vision: {len(rows)} clips ({args.scenes}/context x {len(CONTEXTS)} contexts, {FRAMES} frames), "
        f"{len(ACTIONS)} arms, {args.rounds} rounds x {args.seeds} seeds, lambda={LAMBDA}",
        f"median fake-detector wall time per frame: full {timing['full']} ms, lite {timing['lite']} ms",
        f"best fixed arm (train): {best_fixed}; default arm: {DEFAULT_ARM}",
        "",
        "Learning curve - mean reward per round window (mean±sd over seeds):",
        "policy        " + "".join(f"{f'{a}-{b}':>16}" for a, b in WINDOWS if b <= args.rounds),
    ]
    curves: dict[str, list[float | None]] = {}
    for name, runs in policies.items():
        per_seed = [window_means(c) for c in runs]
        cells = []
        mean_row: list[float | None] = []
        for w, (_a, b) in enumerate(WINDOWS):
            if b > args.rounds:
                continue
            vals = [p[w] for p in per_seed if p[w] is not None]
            m, sd = statistics.fmean(vals), statistics.pstdev(vals)  # type: ignore[arg-type]
            mean_row.append(round(m, 4))
            cells.append(f"{fmt(m, sd):>16}")
        curves[name] = mean_row
        lines.append(f"{name:<14}" + "".join(cells))
    lines += ["", "Held-out clips - greedy policy after training vs fixed arms:"]
    lines.append(f"{'policy':<14}{'F1':>8}{'cost':>8}{'reward':>9}")
    heldout: dict[str, dict[str, float]] = {}
    for name, evs in held.items():
        heldout[name] = {m: statistics.fmean(e[m] for e in evs) for m in ("f1", "cost", "reward")}
    heldout["default"] = evaluate(test_rows, lambda c: DEFAULT_ARM)
    heldout["best_fixed"] = evaluate(test_rows, lambda c: best_fixed)
    heldout["oracle(train)"] = evaluate(test_rows, lambda c: oracle[c])
    for name, e in heldout.items():
        lines.append(f"{name:<14}{e['f1']:>8.3f}{e['cost']:>8.3f}{e['reward']:>+9.3f}")
    lines += ["", "Per-context best arm (train) and its held-out reward vs default:"]
    per_ctx = {}
    for c in CONTEXTS:
        d = evaluate(test[c], lambda _c: DEFAULT_ARM)["reward"]
        o = evaluate(test[c], lambda _c, c=c: oracle[c])["reward"]
        per_ctx[c] = {"best": oracle[c], "default_reward": round(d, 4), "best_reward": round(o, 4)}
        lines.append(f"{c:<20}{oracle[c]:<16}{d:>+8.3f} -> {o:>+7.3f}")
    print("\n".join(lines))
    return {
        "curves": curves,
        "heldout": heldout,
        "per_context": per_ctx,
        "timing_ms": timing,
        "best_fixed": best_fixed,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seeds", type=int, default=10)
    ap.add_argument("--rounds", type=int, default=1500)
    ap.add_argument("--scenes", type=int, default=24, help="clips per context")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--json", type=Path, help="also write the summary as JSON here")
    args = ap.parse_args()
    t0 = time.perf_counter()
    rows, timing = asyncio.run(build_table(args.scenes, args.seed))
    summary = report(args, rows, timing)
    print(f"\n(total {time.perf_counter() - t0:.1f}s)")
    if args.json:
        args.json.write_text(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
