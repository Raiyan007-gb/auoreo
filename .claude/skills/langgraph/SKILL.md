---
name: langgraph
description: Build stateful agent graphs with LangGraph 1.x (Python) — nodes, edges, durable SQLite checkpointing, streaming events, and human-in-the-loop interrupts. Use when writing agent_runtime, session_runner, or research_agent in this repo.
---

# LangGraph 1.x (Python) — building the agent runtime

Current major version: **1.x** (`langgraph` package; also `langgraph-checkpoint`, `langgraph-checkpoint-sqlite`, `langgraph-sdk`). Event streaming `version="v3"` needs **langgraph ≥ 1.2** (beta in 1.2). Docs: https://docs.langchain.com/oss/python/langgraph · Context7: `/websites/langchain_oss_python_langgraph`.

## Core mental model

A compiled graph = typed **state** + **nodes** (functions) + **edges/conditional edges**. State flows through nodes as a `dict`/TypedDict; nodes return partial updates merged into state. **Checkpointing is what enables pause/resume + HITL** — without a checkpointer, `interrupt()` cannot work.

## Minimal ReAct-style loop

```python
from typing import Literal
from langgraph.graph import StateGraph, START, END, MessagesState

def should_continue(state: MessagesState) -> Literal[END, "tools"]:
    last = state["messages"][-1]
    return END if not getattr(last, "tool_calls", None) else "tools"

builder = StateGraph(MessagesState)
builder.add_node("agent", call_model)   # calls the LLM, returns {"messages": [AIMessage]}
builder.add_node("tools", execute_tools)
builder.add_edge(START, "agent")
builder.add_conditional_edges("agent", should_continue)
builder.add_edge("tools", "agent")      # loop back
```

## Checkpointing (required for interrupts/resume)

```python
# uv add langgraph-checkpoint-sqlite   (SQLite saver is a separate package)
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver  # durable, restart-safe (async)
from langgraph.checkpoint.memory import InMemorySaver         # tests only

# from_conn_string takes a plain file path (or ":memory:"), NOT a "sqlite:///" URL
async with AsyncSqliteSaver.from_conn_string("./agent.db") as checkpointer:
    graph = builder.compile(checkpointer=checkpointer)
```

- SQLite checkpoint store is the right default in this repo (matches "SQLite first", plan.md Part E). Postgres saver exists (`langgraph-checkpoint-postgres`) for Phase 3.
- **Async note:** the sync `SqliteSaver` (`langgraph.checkpoint.sqlite`) has no async support — in async graphs (this repo is async-first) use `AsyncSqliteSaver` as above.
- Config: pass `{"configurable": {"thread_id": <session_id>}}` on every invoke/stream call. `thread_id` scopes persistence (opencode maps session ID ↔ thread ID).

## Streaming events

```python
# version="v3" = typed per-channel projections over one event flow (langgraph >= 1.2)
stream = await graph.astream_events(          # sync: graph.stream_events(...)
    {"messages": [{"role": "user", "content": prompt}]},
    config,
    version="v3",
)
async for message in stream.messages:         # one ChatModelStream per LLM call
    async for token in message.text:
        print(token, end="", flush=True)
```

- `stream.messages` → token/text chunks; `stream.values` → state snapshots; `stream.output` → final state; `stream.interrupted` / `stream.interrupts` → HITL pause info. Iterating `stream` itself yields raw protocol events (`event["method"]` = channel: `values`, `updates`, `messages`, `tools`, `lifecycle`, ...).
- `graph.stream(..., stream_mode=[...], version="v2")` (chunks typed by `part["type"]`) still works; docs recommend event streaming for new code.
- For emitting app-level events (our SSE `/api/event` stream per plan.md Part G), wrap the graph run: after each node/LLM step, publish an event to `agent_core/event_service`.

## Human-in-the-loop interrupts

```python
from langgraph.types import Command, interrupt

def needs_approval(state):
    if dangerous_tool_requested(state):
        action = interrupt({"question": "Approve running <tool>?", "args": {...}})
        # Graph HALTS here until a consumer resumes with Command(resume=...)
        return {"approved": action.get("accept")}
    return {}

# After a run pauses: stream.interrupted is True, stream.interrupts holds the payload(s).
# Resume on the same thread_id:
stream = await graph.astream_events(
    Command(resume={"accept": True, "args": {"query": "..."}}),
    config,
    version="v3",
)
```

- `interrupt()` is a durable function: state is checkpointed at the interrupt; resumes from exactly there. Matches the HITL `question`/`permission.requested` features in the plan.
- Only reliable with a checkpointer configured.
- On resume the **whole node re-runs** from the top: keep side effects after `interrupt()` (or in a separate node), make anything before it idempotent, and never wrap `interrupt()` in a bare `try/except` (it pauses by raising).

## Working within this repo (agent_runtime)

- `agent_runtime/` is the **only layer allowed to import LangGraph** (plan.md Part C invariant). The server, events, and clients only ever see your `RunResult`/`Message`/`Event` types.
- Build `research_agent.py` as: `plan → (parallel) web_search/read → synthesize → cite`, using `Command`-based HITL for approval-gated tools.
- Tools used by the graph come from `agent_tools` (typed `AnyTool`), surfaced to LangGraph via `langchain_core.tools.StructuredTool.from_function` or direct node functions.

## Gotchas / rules

1. `version="v3"` (`stream_events` / `astream_events`) is the current streaming API; don't write new code against the legacy `astream_events` v1/v2 event dicts.
2. `thread_id` must be stable per session for resume to work across reconnects.
3. Keep LangGraph out of `agent_schema` and `agent_api`; only `agent_runtime`.
4. Pin `langgraph`/`langgraph-checkpoint`/`langgraph-checkpoint-sqlite` exact versions in `uv.lock`.