"""H4: `auoreo model list|set|test` (spec §18).

`list` shows the routing groups (`settings.models`) with the active default and whether each
deployment has credentials (env var NAMES only, never values). `set` writes `model` /
`small_model` to the settings store (state.db). `test` sends one tiny request through the real gateway
(LiteLLM Router, or the fake one) and reports latency, tokens and the first words."""

from __future__ import annotations

import os
import time
from typing import Annotated, Any

import typer
from rich.markup import escape
from rich.table import Table

from auoreo.cli.common import console, dump_json, err_console, settings, with_services
from auoreo.core.services import Services
from auoreo.core.settings import Settings, write_settings_value

app = typer.Typer(no_args_is_help=True)
JsonOpt = Annotated[bool, typer.Option("--json")]
PROMPT = "Reply with the single word OK."


def _credentials(st: Settings) -> Any:
    if st.gateway != "litellm":
        return lambda _model: (True, "fake gateway")
    from auoreo.core.secrets import SecretStore
    from auoreo.gateway.gateway import credential_status

    secret = SecretStore(st.home, st.keyring).get
    return lambda model: credential_status(model, secret)


@app.command("list")
def list_(as_json: JsonOpt = False) -> None:
    """Routing groups and their deployments; `*` marks the active main / small model."""
    st = settings()
    cred = _credentials(st)
    main, small = st.model or "default", st.small_model or "small"
    rows: list[dict[str, Any]] = []
    for group, g in st.models.items():
        for model in g.models or [""]:
            ready, detail = cred(model) if model else (False, "no deployments")
            rows.append(
                {
                    "group": group,
                    "model": model,
                    "active": [r for r, v in (("main", main), ("small", small)) if v in (group, model)],
                    "ready": ready,
                    "detail": detail,
                }
            )
    if st.model and st.model not in st.models and all(r["model"] != st.model for r in rows):
        ready, detail = cred(st.model)
        rows.insert(
            0, {"group": "-", "model": st.model, "active": ["main"], "ready": ready, "detail": detail}
        )
    if as_json:
        dump_json({"gateway": st.gateway, "model": main, "small_model": small, "models": rows})
        return
    table = Table(caption=f"gateway: {st.gateway} · main: {main} · small: {small}")
    for col in ("", "group", "model", "credentials"):
        table.add_column(col, overflow="fold")
    for r in rows:
        mark = "*" if r["active"] else ""
        table.add_row(
            mark, r["group"], r["model"], ("ok " if r["ready"] else "missing ") + escape(r["detail"])
        )
    console.print(table)


@app.command("set")
def set_(
    model: Annotated[str, typer.Argument(help="Routing group (default, small, …) or <provider>/<model id>.")],
    small: Annotated[bool, typer.Option("--small", help="Set small_model (aux calls) instead.")] = False,
) -> None:
    """Persist the default (or small) model in the settings store."""
    key = "small_model" if small else "model"
    if "/" not in model and model not in settings().models:
        raise typer.BadParameter(f"'{model}' is neither a routing group nor <provider>/<model id>")
    path = write_settings_value(settings().home, key, model)
    console.print(f"{key} = {model} ({path})")
    env = f"AUOREO_{key.upper()}"
    if os.environ.get(env):
        err_console.print(
            f"[yellow]{env} is set in the environment and still wins over the stored value[/yellow]"
        )


@app.command("test")
def test(
    model: Annotated[
        str | None, typer.Argument(help="Group or model id (default: the active model).")
    ] = None,
    timeout: Annotated[float, typer.Option("--timeout", help="Seconds.")] = 60.0,
    as_json: JsonOpt = False,
) -> None:
    """Send one tiny request through the gateway and report the outcome."""
    import asyncio

    from auoreo.gateway.errors import GatewayError
    from auoreo.gateway.types import Finish, LLMRequest, TextDelta, Usage

    st = settings()
    name = model or st.model or "default"

    async def go(svc: Services) -> dict[str, Any]:
        req = LLMRequest(
            model=name,
            messages=({"role": "user", "content": PROMPT},),
            max_tokens=16,
            metadata={"purpose": "model_test"},
        )
        text, usage, finish, t0 = "", None, None, time.monotonic()

        async def consume() -> None:
            nonlocal text, usage, finish
            async for item in svc.gateway.stream(req):
                if isinstance(item, TextDelta):
                    text += item.text
                elif isinstance(item, Usage):
                    usage = item
                elif isinstance(item, Finish):
                    finish = item.reason

        await asyncio.wait_for(consume(), timeout)
        return {
            "model": name,
            "ok": True,
            "latency_ms": round((time.monotonic() - t0) * 1000),
            "finish": finish,
            "text": text.strip()[:200],
            "tokens": usage.event_data() if usage else None,
        }

    try:
        out = with_services(go, st, memory=False)
    except (GatewayError, TimeoutError) as exc:
        out = {"model": name, "ok": False, "error": str(exc) or type(exc).__name__}
    if as_json:
        dump_json(out)
    elif out["ok"]:
        console.print(f"{name}: ok in {out['latency_ms']} ms ({out['finish']}): {escape(out['text'])}")
    else:
        err_console.print(f"[red]{name}: {escape(out['error'])}[/red]")
    if not out["ok"]:
        raise typer.Exit(1)
