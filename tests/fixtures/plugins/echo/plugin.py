"""Echo test plugin (stdlib only)."""

import os
from pathlib import Path


class Plugin:
    def __init__(self, host):
        self.host = host
        self.config = {}

    def initialize(self, config, consumes_granted):
        self.config = config
        return {
            "tools": [
                {
                    "name": "echo",
                    "description": "Echo the given text back.",
                    "parameters": {
                        "type": "object",
                        "properties": {"text": {"type": "string"}},
                        "required": ["text"],
                    },
                },
                {"name": "crash", "description": "Exit the plugin process abruptly."},
                {
                    "name": "secret",
                    "description": "Read a secret through the host.",
                    "parameters": {"type": "object", "properties": {"name": {"type": "string"}}},
                },
                {"name": "slow", "description": "Never answers in time.", "timeout_ms": 300},
            ],
            "deliveries": ["echo_log"],
            "connectors": ["echo_chat"],
            "skills": [{"name": "echo-usage", "description": "How to echo", "body": "Call echo."}],
        }

    async def call_tool(self, name, args, session_id):
        if name == "echo":
            who = os.environ.get("AUOREO_PLUGIN")
            return f"{self.config.get('greeting', 'echo')}: {args.get('text', '')} [{who}]"
        if name == "crash":
            os._exit(3)
        if name == "slow":
            import asyncio

            await asyncio.sleep(30)
            return "late"
        try:
            value = await self.host.secret(args.get("name", ""))
        except Exception as exc:
            return {"content": f"denied: {exc}", "is_error": True}
        return f"secret={value}"

    def send(self, name, target, text, meta):
        path = self.config.get("log_path")
        if not path:
            return {"ok": False, "detail": "no log_path configured"}
        with Path(path).open("a", encoding="utf-8") as fh:
            fh.write(f"{target}\t{text}\n")
        return {"ok": True, "detail": "written", "external_id": f"line-{target}"}

    def connector_start(self, name):
        if self.config.get("inbound"):
            self.host.message(name, "chat-1", "alice", "hello from echo", reply_to=None)
        return {}

    def connector_stop(self, name):
        return {}

    def config_update(self, config):
        self.config = config
        return {}
