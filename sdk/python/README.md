# AUOREO Python SDK

- `auoreo_client/` — **generated** by [openapi-python-client](https://github.com/openapi-generators/openapi-python-client)
  from `web/sdk/openapi.json`. Do not edit by hand; regenerate with `scripts/gen_sdk.sh`
  (the generator version is pinned there). CI (`.github/workflows/sdk.yml`) fails when it drifts.
- `auoreo_sdk_extras/` — a thin hand-written layer: `subscribe_events` (SSE with
  `Last-Event-ID` resume) and `chat_and_wait`.

Dependencies: `httpx`, `attrs`, `python-dateutil` (what the generated code imports).

```python
import asyncio
import sys

sys.path.insert(0, "sdk/python")  # or copy both packages into your project

from auoreo_client import AuthenticatedClient
from auoreo_client.api.health import health_api_health_get
from auoreo_client.api.sessions import create_session_api_sessions_post
from auoreo_client.models import SessionCreate
from auoreo_sdk_extras import chat_and_wait, subscribe_events


async def main() -> None:
    client = AuthenticatedClient(base_url="http://127.0.0.1:4096", token="<AUOREO_SERVER_PASSWORD>")
    print(await health_api_health_get.asyncio(client=client))
    session = await create_session_api_sessions_post.asyncio(client=client, body=SessionCreate(title="sdk"))
    print(await chat_and_wait(client, "hello", session_id=session.id))

    def on_event(ev: dict) -> bool:
        print(ev["seq"], ev["type"])
        return ev["type"] == "run.completed"  # True stops the subscription

    await subscribe_events("http://127.0.0.1:4096", "<token>", 0, on_event, session_ids=[session.id])


asyncio.run(main())
```

When no server password is set, `Client(base_url=...)` works too (the token is ignored).
