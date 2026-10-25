"""Face detection (default off). Embeddings stay in the in-memory vault; only refs and
similarities are returned, so nothing biometric reaches the transcript, DB or memory."""

import asyncio

from auoreo.perception.draw import annotate, box_dict, render_boxes
from auoreo.perception.faces import TTL_S, cosine, vault_for
from auoreo.perception.inputs import load_image
from auoreo.tools.context import ToolContext, ToolError, ToolResult
from auoreo.tools.images import to_part

DISABLED = (
    "face detection is disabled (settings.faces_enabled is false). Faces are off by default "
    'for privacy; a user can enable them with "faces_enabled": true in settings.json.'
)


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    svc = ctx.services.get("services")
    if svc is None or not getattr(svc.settings, "faces_enabled", False):
        raise ToolError(DISABLED)
    vault = vault_for(svc)
    if args.get("forget"):
        await vault.close_session(ctx.session_id)
    compare_to = [str(r) for r in args.get("compare_to") or []]
    want_emb = bool(args.get("embeddings")) or bool(compare_to)
    data, label = await load_image(ctx, "detect_faces", args)
    backend = ctx.backend("face")
    faces = await backend.faces(data, embeddings=want_emb)
    name = getattr(backend, "name", "face")
    lines = [f"[faces: {name}] {len(faces)} face(s) in {label}" + (":" if faces else ".")]
    rows = []
    refs: list[str | None] = []
    for f in faces:
        line = render_boxes([f.box])
        row = box_dict(f.box)
        if args.get("landmarks") and f.landmarks:
            pts = [[round(x, 1), round(y, 1)] for x, y in f.landmarks]
            line += f" landmarks={pts}"
            row["landmarks"] = pts
        ref = vault.put(ctx.session_id, f.embedding) if want_emb and f.embedding is not None else None
        refs.append(ref)
        if ref:
            line += f" ref={ref}"
            row["ref"] = ref
        lines.append(line)
        rows.append(row)
    if want_emb:
        if not getattr(backend, "supports_embeddings", True) or not any(refs):
            lines.append(f"(backend '{name}' does not produce embeddings)")
        else:
            lines.append(f"(embeddings held in memory only for {int(TTL_S // 60)} min; never stored)")
        lines.extend(_similarities(vault, ctx.session_id, [r for r in refs if r], compare_to))
    images = []
    if args.get("annotate"):
        png = await asyncio.to_thread(annotate, data, [f.box for f in faces])
        part, _meta = await to_part(png, f"{label} with {len(faces)} face box(es)")
        images.append(part)
    return ToolResult(
        "\n".join(lines), meta={"backend": name, "count": len(faces), "faces": rows}, images=images
    )


def _similarities(vault, session_id: str, new: list[str], earlier: list[str]) -> list[str]:
    out: list[str] = []
    pairs = [(a, b) for i, a in enumerate(new) for b in new[i + 1 :]] + [(a, b) for a in new for b in earlier]
    for a, b in pairs:
        ea, eb = vault.get(session_id, a), vault.get(session_id, b)
        if eb is None:
            out.append(f"similarity {a}~{b}: unknown ref (expired or forgotten)")
        elif ea is not None:
            out.append(f"similarity {a}~{b}: {cosine(ea, eb):.3f}")
    return out
