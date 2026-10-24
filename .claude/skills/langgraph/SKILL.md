---
name: langgraph
description: Build stateful agent graphs with LangGraph 1.x (Python) — nodes, edges, durable SQLite checkpointing, streaming events, and human-in-the-loop interrupts. Use when writing agent_runtime, session_runner, or research_agent in this repo.
---

# LangGraph 1.x (Python) — building the agent runtime

Current major version: **1.x** (`langgraph` package; also `langgraph-checkpoint`, `langgraph-sdk`). Docs: https://docs.langchain.com/oss/python/langgraph · Context7: `/websites/langchain_oss_python_langgraph`.

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
from langgraph.checkpoint.sqlite import SqliteSaver   # durable, restarts-safe
from langgraph.checkpoint.memory import InMemorySaver # tests only

with SqliteSaver.from_conn_string("sqlite:///./agent.db") as checkpointer:
    graph = builder.compile(checkpointer=checkpointer)
```

- SQLite checkpoint store is the right default in this repo (matches "SQLite first", plan.md Part E). Postgres saver exists (`langgraph-checkpoint-postgres`) for Phase 3.
- **Async note:** `SqliteSaver` has no async support — in async graphs (this repo is async-first) use `AsyncSqliteSaver` from `langgraph.checkpoint.sqlite.aio`.
- Config: pass `{"configurable": {"thread_id": <session_id>}}` on every invoke/stream call. `thread_id` scopes persistence (opencode maps session ID ↔ thread ID).

## Streaming events

```python
# version="v3" enables concurrent message-chunk + state-snapshot consumption
stream = graph.stream_events(
    {"messages": [{"role": "user", "content": prompt}]},
    config,
    version="v3",
)
for message in stream.messages:
    for token in message.text:
        print(token, end="", flush=True)
```

- `stream.messages` → token/text chunks; `stream.values` → state snapshots after each node.
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

# Resuming from an interrupted run:
result = graph.stream(
    Command(resume={"accept": True, "args": {"query": "..."}}),
    config,
)
```

- `interrupt()` is a durable function: state is checkpointed at the interrupt; resumes from exactly there. Matches the HITL `question`/`permission.requested` features in the plan.
- Only reliable with a checkpointer configured.

## Working within this repo (agent_runtime)

- `agent_runtime/` is the **only layer allowed to import LangGraph** (plan.md Part C invariant). The server, events, and clients only ever see your `RunResult`/`Message`/`Event` types.
- Build `research_agent.py` as: `plan → (parallel) web_search/read → synthesize → cite`, using `Command`-based HITL for approval-gated tools.
- Tools used by the graph come from `agent_tools` (typed `AnyTool`), surfaced to LangGraph via `langchain_core.tools.StructuredTool.from_function` or direct node functions.

## Gotchas / rules

1. Version "v3" streaming in Python is the current API (do NOT use old `astream_events` chained flatMap patterns).
2. `thread_id` must be stable per session for resume to work across reconnects.
3. Keep LangGraph out of `agent_schema` and `agent_api`; only `agent_runtime`.
4. Pin `langgraph`/`langgraph-checkpoint` exact versions in `uv.lock`.