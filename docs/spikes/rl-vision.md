# Spike: RL-vision / sensor policy (spec L604, M6 "bandit + RL-vision")

**Decision:** do **not** ship an RL or bandit policy for perception now, and do not add a
`perception_policy` flag. The offline numbers show that almost all of the gain comes from
**retuning one static default**. A context-aware lookup (threshold by lighting, frame interval
by motion) captures the rest. An online bandit adds little on top, needs labelled reward that
AUOREO does not observe at runtime, and costs reward while it explores.

## Context

The M6 backlog lists "bandit + RL-vision": letting a learned policy pick perception actions
(which detector, which score threshold, how often to run it) per sensor context, the way
`src/auoreo/policy/bandit.py` picks model and effort per turn.

## Experiment (`scripts/spikes/rl_vision.py`)

```bash
uv run python scripts/spikes/rl_vision.py --scenes 24 --seeds 10 --rounds 3000
```

- **Detector:** the real `fake` detection backend (`backends/detection/fake`, the Pillow
  colour-blob detector whose scores behave like a real detector: `score = fill × contrast`).
- **Scenes:** 192 synthetic 160×120 clips of 6 frames each, 24 per context. There are 8
  contexts: lighting {bright, dim} × clutter {low, high} × motion {static, fast}.
  - Targets are solid rectangles (tall = `person`).
  - Dim light lowers target contrast, and so the score.
  - Clutter adds rings, discs and diagonals. The detector also reports these, but they are not
    ground truth.
  - Fast motion moves targets about 10 px per frame.
- **Actions (20 arms):** backend {`full`, `lite` = same detector on a 0.4× frame} × threshold
  {0.3, 0.45, 0.6, 0.75, 0.9} × frame interval {1, 3}. Boxes are held over skipped frames.
- **Reward per clip:** `F1(IoU ≥ 0.5, label-aware) − 0.3 × cost`. Cost is the fraction of a
  "full detector on every frame" budget: full = 1.0 per call, lite = 0.35.
- **Protocol:** each (clip, arm) outcome is precomputed once, since the detector is
  deterministic. Clips are split 2/3 train and 1/3 held-out per context. The policies replay
  over the train pool for 3000 rounds × 10 seeds:
  - `contextual`: `auoreo.policy.bandit.choose`, ε-greedy with the shipped defaults (ε 0.15,
    −0.02 per 100 pulls, floor 0.02), one stats table per context.
  - `global`: the same with a single table.
  - `default`: what `detect_objects` does today: full detector, threshold 0.3, every frame.
  - `best_fixed`: the best single arm on the train pool, in hindsight.
  - `oracle`: the best arm per context, in hindsight.

## Measurements (this container, 7.4 s total)

Median fake-detector wall time per frame: **full 3.66 ms, lite 1.42 ms**.

Learning curve: mean reward per window of rounds (mean ± sd over 10 seeds):

| policy | 1–100 | 101–250 | 251–500 | 501–1000 | 1001–1500 | 1501–3000 |
|---|---|---|---|---|---|---|
| contextual bandit | +0.324±0.035 | +0.503±0.034 | +0.701±0.018 | +0.696±0.013 | +0.709±0.021 | +0.727±0.010 |
| global bandit | +0.531±0.050 | +0.643±0.028 | +0.673±0.038 | +0.697±0.029 | +0.695±0.031 | +0.696±0.029 |
| default (full/0.3/every frame) | +0.437±0.028 | +0.439±0.033 | +0.426±0.010 | +0.429±0.013 | +0.421±0.013 | +0.423±0.004 |
| best fixed arm (lite/0.45/k1) | +0.722±0.034 | +0.725±0.021 | +0.721±0.011 | +0.715±0.010 | +0.716±0.009 | +0.718±0.005 |
| per-context oracle | +0.798±0.017 | +0.810±0.013 | +0.798±0.010 | +0.786±0.007 | +0.797±0.008 | +0.792±0.005 |

Held-out clips (greedy policy after 3000 rounds, mean over seeds):

| policy | F1 | cost | reward |
|---|---|---|---|
| contextual bandit | 0.780 | 0.256 | +0.703 |
| global bandit | 0.755 | 0.350 | +0.650 |
| default | 0.706 | 1.000 | +0.406 |
| best fixed arm | 0.767 | 0.350 | +0.662 |
| per-context oracle (train) | 0.780 | 0.233 | +0.710 |

Best arm per context (train): bright → threshold 0.75; dim → 0.45; static → interval 3;
fast → interval 1; `lite` everywhere. The two-rule table "threshold by lighting, interval by
motion" is exactly the oracle.

## Reading the numbers

1. **The default is the problem, not the lack of learning.** `full / 0.3 / every frame`
   scores +0.41 held-out. Picking one better static arm gives +0.66 (**+0.26**), and its F1
   also rises from 0.706 to 0.767, because threshold 0.3 lets clutter through.
2. **Context helps a little.** The contextual bandit's greedy policy reaches +0.70 held-out,
   **+0.04** over the best fixed arm. Most of that is cost (0.256 vs 0.350, from skipping
   frames in static scenes); F1 gains only +0.013.
3. **Exploration costs reward.** The contextual bandit is *worse* than the best fixed arm for
   the first ~250 rounds (+0.32 then +0.50, against +0.72), and only pulls ahead after about
   1500 labelled rounds. A real deployment has far fewer labelled rounds per context.
4. **The reward is not observable online.** F1 needs ground-truth boxes. At runtime
   `detect_objects` has no labels, and the only proxies would be user corrections (rare,
   delayed) or self-consistency across frames. A proxy-reward bandit would optimise the proxy,
   not detection quality. The model/effort bandit works because turn outcome, cost and latency
   *are* observed.
5. **Caveats.** The scenes are synthetic and the detector is the fake one. Real detectors
   (RF-DETR, YOLO) have calibrated scores and other failure modes. The numbers support the
   *shape* of the conclusion (static retuning ≫ online learning), not the exact thresholds.

## Decision

- **No `perception_policy` setting and no bandit in `tools/detect_objects`.** The numbers do
  not justify a flag: +0.04 reward offline, under a reward signal the tool cannot measure
  online.
- **Not changed here:** `detect_objects`' default threshold (0.3). These numbers come from the
  fake detector on synthetic scenes, and real backends need their own calibration. The finding
  does suggest that per-backend default thresholds in `backend.yaml` are the cheap, high-value
  move. That is a follow-up.

## Follow-ups

1. Per-backend `default_threshold` (and a recommended frame interval for sensor sessions) in
   `backends/detection/*/backend.yaml`, calibrated on a small labelled set per real backend.
2. Collect labels before revisiting RL: let perception sessions record user corrections
   (missed or false box) as events. Once a few hundred per context exist, re-run this script
   on recorded clips (the table builder only needs frames + boxes).
3. If that data exists, start with the context rule table (lighting → threshold, motion →
   interval) learned offline. Use an online bandit only if contexts drift.
