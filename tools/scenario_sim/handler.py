"""Seeded Monte Carlo: sample declared variables, evaluate a sandboxed arithmetic expression.

The expression is parsed with `ast` and accepted only if every node is on a whitelist
(numbers, variable names, `+ - * / **`, unary `+ -`, and calls to `min max abs round` by
bare name). Attribute access, subscripts, other calls, dunder names, lambdas and
comprehensions are rejected before anything runs. All numbers are floats, so `**`
overflows (an error) instead of building huge integers. Only stdlib `random` is used.
"""

from __future__ import annotations

import ast
import asyncio
import math
import random
import re
from collections.abc import Callable
from typing import Any

from auoreo.tools.context import ToolContext, ToolError, ToolResult

MAX_ITERATIONS = 100_000
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)
_UNARY = (ast.UAdd, ast.USub)


def _round(x: float, n: float = 0) -> float:
    return float(round(x, int(n)))


FUNCS: dict[str, Callable[..., float]] = {"min": min, "max": max, "abs": abs, "round": _round}


def _check(node: ast.AST, names: set[str]) -> None:
    checker = _CHECKS.get(type(node))
    if checker is None:
        raise ToolError(f"{type(node).__name__} is not allowed in the expression")
    checker(node, names)


def _expression(node: Any, names: set[str]) -> None:
    _check(node.body, names)


def _constant(node: Any, names: set[str]) -> None:
    if isinstance(node.value, bool) or not isinstance(node.value, int | float):
        raise ToolError(f"only numeric constants are allowed (got {node.value!r})")


def _name(node: Any, names: set[str]) -> None:
    if node.id not in names:
        raise ToolError(f"unknown name '{node.id}' (declared variables: {', '.join(sorted(names))})")


def _binop(node: Any, names: set[str]) -> None:
    if not isinstance(node.op, _BINOPS):
        raise ToolError(f"operator {type(node.op).__name__} is not allowed (use + - * / **)")
    _check(node.left, names)
    _check(node.right, names)


def _unary(node: Any, names: set[str]) -> None:
    if not isinstance(node.op, _UNARY):
        raise ToolError(f"operator {type(node.op).__name__} is not allowed")
    _check(node.operand, names)


def _call(node: Any, names: set[str]) -> None:
    if not isinstance(node.func, ast.Name) or node.func.id not in FUNCS or node.keywords:
        raise ToolError("calls are limited to min(), max(), abs() and round() with positional arguments")
    if not node.args:
        raise ToolError(f"{node.func.id}() needs arguments")
    for arg in node.args:
        _check(arg, names)


def _attribute(node: Any, names: set[str]) -> None:
    raise ToolError("attribute access is not allowed")


_CHECKS: dict[type[ast.AST], Callable[[Any, set[str]], None]] = {
    ast.Expression: _expression,
    ast.Constant: _constant,
    ast.Name: _name,
    ast.BinOp: _binop,
    ast.UnaryOp: _unary,
    ast.Call: _call,
    ast.Attribute: _attribute,
}


class _Floats(ast.NodeTransformer):
    def visit_Constant(self, node: ast.Constant) -> ast.Constant:
        return ast.copy_location(ast.Constant(float(node.value)), node)


def compile_expression(expression: str, names: set[str]) -> Callable[[dict[str, float]], float]:
    try:
        tree = ast.parse(expression.strip(), mode="eval")
    except SyntaxError as exc:
        raise ToolError(f"expression is not valid arithmetic: {exc.msg}") from exc
    _check(tree, names)
    code = compile(ast.fix_missing_locations(_Floats().visit(tree)), "<scenario>", "eval")
    env: dict[str, Any] = {"__builtins__": {}, **FUNCS}

    def evaluate(values: dict[str, float]) -> float:
        return float(eval(code, env, values))  # noqa: S307 - whitelisted AST, no builtins

    return evaluate


def _need(spec: dict[str, Any], name: str, *keys: str) -> list[float]:
    missing = [k for k in keys if k not in spec]
    if missing:
        raise ToolError(f"variable '{name}' ({spec['dist']}) needs {', '.join(missing)}")
    return [float(spec[k]) for k in keys]


def sampler(name: str, spec: dict[str, Any]) -> Callable[[random.Random], float]:
    dist = spec["dist"]
    if dist == "choice":
        values = [float(v) for v in spec.get("values") or []]
        weights = spec.get("weights")
        if not values:
            raise ToolError(f"variable '{name}' (choice) needs values")
        if weights is not None and (len(weights) != len(values) or sum(weights) <= 0):
            raise ToolError(f"variable '{name}': weights must match values and sum > 0")
        return lambda rng: rng.choices(values, weights)[0]
    makers: dict[str, Callable[[], Callable[[random.Random], float]]] = {
        "uniform": lambda: _uniform(name, *_need(spec, name, "low", "high")),
        "normal": lambda: _normal(*_need(spec, name, "mean", "sd")),
        "triangular": lambda: _triangular(name, *_need(spec, name, "low", "high", "mode")),
    }
    return makers[dist]()


def _uniform(name: str, low: float, high: float) -> Callable[[random.Random], float]:
    if high < low:
        raise ToolError(f"variable '{name}': high must be ≥ low")
    return lambda rng: rng.uniform(low, high)


def _normal(mean: float, sd: float) -> Callable[[random.Random], float]:
    return lambda rng: rng.gauss(mean, sd)


def _triangular(name: str, low: float, high: float, mode: float) -> Callable[[random.Random], float]:
    if not low <= mode <= high:
        raise ToolError(f"variable '{name}': need low ≤ mode ≤ high")
    return lambda rng: rng.triangular(low, high, mode)


def percentile(sorted_xs: list[float], p: float) -> float:
    k = (len(sorted_xs) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    return sorted_xs[lo] + (sorted_xs[hi] - sorted_xs[lo]) * (k - lo)


def simulate(
    variables: dict[str, dict[str, Any]],
    expression: str,
    *,
    iterations: int = 10_000,
    seed: int = 0,
    threshold: float | None = None,
) -> dict[str, Any]:
    if not 1 <= iterations <= MAX_ITERATIONS:
        raise ToolError(f"iterations must be 1–{MAX_ITERATIONS}")
    bad = [n for n in variables if not NAME_RE.fullmatch(n) or n in FUNCS]
    if bad:
        raise ToolError(f"invalid variable names {bad}: use [a-z][a-z0-9_]* (not min/max/abs/round)")
    evaluate = compile_expression(expression, set(variables))
    samplers = [(n, sampler(n, variables[n])) for n in sorted(variables)]
    rng = random.Random(seed)
    xs: list[float] = []
    for i in range(iterations):
        values = {n: draw(rng) for n, draw in samplers}
        try:
            x = evaluate(values)
        except (ZeroDivisionError, OverflowError, ValueError) as exc:
            raise ToolError(f"expression failed at iteration {i + 1} ({exc}); values {values}") from exc
        if math.isnan(x) or math.isinf(x):
            raise ToolError(f"expression is not finite at iteration {i + 1}; values {values}")
        xs.append(x)
    xs.sort()
    mean = math.fsum(xs) / len(xs)
    sd = math.sqrt(math.fsum((x - mean) ** 2 for x in xs) / len(xs))
    out: dict[str, Any] = {
        "iterations": iterations,
        "seed": seed,
        "mean": mean,
        "sd": sd,
        "min": xs[0],
        "p5": percentile(xs, 0.05),
        "p50": percentile(xs, 0.50),
        "p95": percentile(xs, 0.95),
        "max": xs[-1],
    }
    if threshold is not None:
        out["threshold"] = threshold
        out["p_above"] = sum(1 for x in xs if x > threshold) / len(xs)
    return out


def render(expression: str, r: dict[str, Any]) -> str:
    lines = [
        f"Monte Carlo of `{expression}` — {r['iterations']} iterations, seed {r['seed']}",
        "",
        "| mean | sd | p5 | p50 | p95 | min | max |",
        "|---|---|---|---|---|---|---|",
        "| " + " | ".join(f"{r[k]:.4g}" for k in ("mean", "sd", "p5", "p50", "p95", "min", "max")) + " |",
    ]
    if "p_above" in r:
        lines += ["", f"P(expression > {r['threshold']:g}) = {r['p_above']:.1%}"]
    return "\n".join(lines)


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    result = await asyncio.to_thread(
        simulate,
        args["variables"],
        args["expression"],
        iterations=int(args.get("iterations", 10_000)),
        seed=int(args.get("seed", 0)),
        threshold=args.get("threshold"),
    )
    return ToolResult(render(args["expression"], result), meta=result)
