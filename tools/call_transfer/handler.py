"""Cold / warm call transfer through the livekit `rtc_transport` backend (F5, spec §26.8).

Refuses unless the selected rtc transport is `livekit`. The backend emits `rtc.transfer` and
audits `rtc.transfer` (target number masked to its last four digits)."""

from auoreo.tools.context import ToolContext, ToolError, ToolResult


def _room(inst: object, args: dict, ctx: ToolContext) -> str:
    if args.get("room"):
        return str(args["room"])
    rooms = getattr(inst, "rooms", {})
    for name, info in rooms.items():
        if info.get("session_id") == ctx.session_id:
            return str(name)
    raise ToolError("no LiveKit room is joined for this session; pass `room`")


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    inst = ctx.backend("rtc_transport")
    name = getattr(inst, "name", type(inst).__name__)
    if name != "livekit" or not hasattr(inst, "warm_transfer"):
        raise ToolError(
            f"call_transfer needs the livekit rtc transport (selected: {name}); "
            'set settings.backends.rtc_transport = "livekit"'
        )
    svc = ctx.services.get("services")
    if svc is not None:
        inst.bind(svc)
    room, caller, kind = _room(inst, args, ctx), str(args["caller"]), args["kind"]
    try:
        if kind == "cold":
            uri = inst.tel_uri(args["target"])
            await inst.transfer(room, caller, uri, play_dialtone=bool(args.get("play_dialtone", True)))
            return ToolResult(
                f"cold transfer of {caller} in {room} started (SIP REFER)", meta={"kind": "cold"}
            )
        out = await inst.warm_transfer(room, caller, args["target"], announce_text=args.get("announce"))
    except (ValueError, ConnectionError, PermissionError, RuntimeError) as exc:
        raise ToolError(f"transfer failed: {exc}") from None
    said = "announced" if out["announced"] else "no announcement (TTS/realtime SDK unavailable or none given)"
    return ToolResult(
        f"warm transfer of {caller} in {room} done via {out['bridge']}; {said}; agent left the call",
        meta=out,
    )
