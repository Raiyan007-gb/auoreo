"""GPU reading via NVML (`pynvml` from the `nvidia-ml-py` package, extra `ops`; never
GPUtil). Imported lazily; no package / no driver / no GPU is an honest, non-error result
(meta `available: false`) so health checks on GPU-less machines do not fail."""

import asyncio
from typing import Any

from auoreo.tools.context import ToolContext, ToolResult

MIB = 1024 * 1024


def _try(fn: Any, *args: Any) -> Any:
    try:
        return fn(*args)
    except Exception:  # NVMLError_NotSupported and friends: one metric, not the reading
        return None


def _text(v: Any) -> str:
    return v.decode(errors="replace") if isinstance(v, bytes) else str(v)


def _device(nv: Any, i: int, processes: bool) -> list[str]:
    h = nv.nvmlDeviceGetHandleByIndex(i)
    name = _text(_try(nv.nvmlDeviceGetName, h) or "?")
    util = _try(nv.nvmlDeviceGetUtilizationRates, h)
    mem = _try(nv.nvmlDeviceGetMemoryInfo, h)
    temp = _try(nv.nvmlDeviceGetTemperature, h, getattr(nv, "NVML_TEMPERATURE_GPU", 0))
    power = _try(nv.nvmlDeviceGetPowerUsage, h)  # milliwatts
    limit = _try(nv.nvmlDeviceGetEnforcedPowerLimit, h)
    parts = [f"- **GPU {i}** {name}"]
    parts.append(f"util {util.gpu}% (mem bus {util.memory}%)" if util is not None else "util n/a")
    if mem is not None:
        pct = 100 * mem.used / max(mem.total, 1)
        parts.append(f"memory {mem.used / MIB:.0f}/{mem.total / MIB:.0f} MiB ({pct:.0f}%)")
    parts.append(f"{temp}°C" if temp is not None else "temp n/a")
    if power is not None:
        parts.append(f"power {power / 1000:.0f} W" + (f" / {limit / 1000:.0f} W" if limit else ""))
    lines = [" · ".join(parts)]
    if processes:
        procs = _try(nv.nvmlDeviceGetComputeRunningProcesses, h) or []
        for p in procs[:20]:
            used = getattr(p, "usedGpuMemory", None)
            lines.append(f"  - pid {p.pid}: {used / MIB:.0f} MiB" if used else f"  - pid {p.pid}")
    return lines


def _read(processes: bool) -> ToolResult:
    try:
        import pynvml as nv
    except ImportError:
        return ToolResult(
            "**GPU** unavailable: pynvml is not installed (install the `ops` extra: nvidia-ml-py)",
            meta={"available": False, "reason": "pynvml-missing"},
        )
    try:
        nv.nvmlInit()
    except Exception as exc:  # NVMLError_LibraryNotFound / DriverNotLoaded / NoPermission
        return ToolResult(
            f"**GPU** no NVIDIA GPU or driver available (NVML: {exc})",
            meta={"available": False, "reason": "no-nvidia", "detail": str(exc)},
        )
    try:
        count = int(nv.nvmlDeviceGetCount())
        driver = _text(_try(nv.nvmlSystemGetDriverVersion) or "?")
        if count == 0:
            return ToolResult(
                f"**GPU** NVML found no devices (driver {driver})", meta={"available": False, "gpus": 0}
            )
        lines = [f"**GPU** {count} NVIDIA device(s), driver {driver}"]
        for i in range(count):
            lines += _device(nv, i, processes)
        return ToolResult("\n".join(lines), meta={"available": True, "gpus": count})
    finally:
        _try(nv.nvmlShutdown)


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    return await asyncio.to_thread(_read, bool(args.get("processes", False)))
