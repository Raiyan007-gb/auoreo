import json

from auoreo.teams.tooling import attempt, teams_for
from auoreo.tools.context import ToolContext, ToolResult


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    data = await attempt(teams_for(ctx).kanban.show(args["task_id"]))
    worker = f" worker={data['worker']}" if data["worker"] else ""
    lines = [f"#{data['id']} [{data['status']}] {data['title']} (board={data['board']}{worker})"]
    if data["body"]:
        lines.append(data["body"])
    lines += [f"link: {link['kind']} #{link['task_id']}" for link in data["links"]]
    lines += [
        f"attachment: {a['ref']}" + (f" — {a['note']}" if a["note"] else "") for a in data["attachments"]
    ]
    lines += [f"comment ({c['author']}): {c['text']}" for c in data["comments"]]
    return ToolResult("\n".join(lines), meta=json.loads(json.dumps(data, default=str)))
