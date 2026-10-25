"""Weighted decision matrix with a one-criterion-at-a-time sensitivity analysis.

Weights are normalised to sum 1; `direction: min` criteria score as `10 - score`. For each
criterion c the other weights keep their proportions while w_c moves over [0, 1], so every
option's total is linear in w_c; the nearest w_c where a challenger ties the winner is the
weight change that flips the decision.
"""

from __future__ import annotations

from typing import Any

from auoreo.tools.context import ToolContext, ToolError, ToolResult

EPS = 1e-9


def _fmt_pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def evaluate(criteria: list[dict[str, Any]], options: list[dict[str, Any]]) -> dict[str, Any]:
    names = [c["name"] for c in criteria]
    if len(set(names)) != len(names):
        raise ToolError("criterion names must be unique")
    if len({o["name"] for o in options}) != len(options):
        raise ToolError("option names must be unique")
    total_w = sum(float(c["weight"]) for c in criteria)
    if total_w <= 0:
        raise ToolError("weights must be positive")
    weights = {c["name"]: float(c["weight"]) / total_w for c in criteria}
    eff: dict[str, dict[str, float]] = {}
    for o in options:
        missing = [n for n in names if n not in o["scores"]]
        unknown = [k for k in o["scores"] if k not in weights]
        if missing or unknown:
            raise ToolError(f"option '{o['name']}': missing scores {missing}, unknown criteria {unknown}")
        row: dict[str, float] = {}
        for c in criteria:
            s = float(o["scores"][c["name"]])
            if not 0 <= s <= 10:
                raise ToolError(f"option '{o['name']}': score for '{c['name']}' must be 0–10")
            row[c["name"]] = 10 - s if c.get("direction", "max") == "min" else s
        eff[o["name"]] = row
    totals = {o: sum(weights[c] * s for c, s in row.items()) for o, row in eff.items()}
    ranking = sorted(totals, key=lambda o: (-totals[o], o))
    return {
        "weights": weights,
        "effective": eff,
        "totals": totals,
        "ranking": ranking,
        "sensitivity": sensitivity(weights, eff, ranking[0]),
    }


def sensitivity(
    weights: dict[str, float], eff: dict[str, dict[str, float]], winner: str
) -> list[dict[str, Any]]:
    """Per criterion: the nearest normalised weight at which another option overtakes."""
    out: list[dict[str, Any]] = []
    for c, w in weights.items():
        best: dict[str, Any] | None = None
        rest = 1 - w
        for o in eff:
            if o == winner or rest < EPS:
                continue
            # total(w') = w' * s_c + (1 - w') * R, with R the renormalised rest score
            r_o = sum(weights[k] * eff[o][k] for k in weights if k != c) / rest
            r_w = sum(weights[k] * eff[winner][k] for k in weights if k != c) / rest
            d_s, d_r = eff[o][c] - eff[winner][c], r_o - r_w
            if abs(d_r - d_s) < EPS:
                continue
            flip = d_r / (d_r - d_s)
            if not 0 <= flip <= 1 or abs(flip - w) < EPS:
                continue
            if best is None or abs(flip - w) < abs(best["at"] - w):
                best = {"criterion": c, "from": w, "at": flip, "challenger": o}
        out.append(best or {"criterion": c, "from": w, "at": None, "challenger": None})
    return out


def render(result: dict[str, Any], criteria: list[dict[str, Any]]) -> str:
    names = [c["name"] for c in criteria]
    w = result["weights"]
    head = "| Rank | Option | " + " | ".join(f"{n} ({_fmt_pct(w[n])})" for n in names) + " | Score |"
    lines = [head, "|" + "---|" * (len(names) + 3)]
    for i, o in enumerate(result["ranking"], 1):
        cells = " | ".join(f"{result['effective'][o][n]:.1f}" for n in names)
        lines.append(f"| {i} | {o} | {cells} | **{result['totals'][o]:.2f}** |")
    winner = result["ranking"][0]
    flips = sorted(
        (s for s in result["sensitivity"] if s["at"] is not None), key=lambda s: abs(s["at"] - s["from"])
    )
    lines += ["", f"**Winner: {winner}** ({result['totals'][winner]:.2f} / 10)", "", "Sensitivity:"]
    for s in flips:
        verb = "raising" if s["at"] > s["from"] else "lowering"
        lines.append(
            f"- {verb} **{s['criterion']}** from {_fmt_pct(s['from'])} to {_fmt_pct(s['at'])} "
            f"(others kept in proportion) makes **{s['challenger']}** win"
        )
    robust = [s["criterion"] for s in result["sensitivity"] if s["at"] is None]
    if robust:
        lines.append(f"- no single weight change on {', '.join(robust)} flips the winner")
    if any(c.get("direction") == "min" for c in criteria):
        lines += ["", "_Scores of `min` criteria are shown inverted (10 − score) so higher is better._"]
    return "\n".join(lines)


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    result = evaluate(args["criteria"], args["options"])
    meta = {
        "ranking": result["ranking"],
        "totals": {k: round(v, 4) for k, v in result["totals"].items()},
        "sensitivity": [
            {**s, "from": round(s["from"], 4), "at": None if s["at"] is None else round(s["at"], 4)}
            for s in result["sensitivity"]
        ],
    }
    return ToolResult(render(result, args["criteria"]), meta=meta)
