---
name: litellm
description: Call 100+ LLM providers through one OpenAI-compatible interface with LiteLLM — streaming, retries, budgets, cost tracking, and the proxy server. Use when building agent_gateway in this repo.
---

# LiteLLM — model gateway

Docs: https://docs.litellm.ai · Context7: `/websites/litellm_ai` (reference) and `/berriai/litellm` (repo). Package: `litellm` (Python SDK), `litellm[proxy]` (gateway server).

## Two ways to use it

1. **SDK** (in-process, what this repo's `agent_gateway` uses): `litellm.completion(...)` or `acompletion(...)` — routes any model string like `anthropic/claude-sonnet-...`, `openai/gpt-...`, `ollama/llama3.1`, `openrouter/...`.
2. **Proxy server** (deployment): `/chat/completions` OpenAI-compatible endpoint at port 4000, config-driven `model_list`.

## SDK pattern (agent_gateway gateway.py)

```python
import litellm
from typing import AsyncIterator


async def stream_chat(model: str, messages: list[dict], **kwargs) -> AsyncIterator[str]:
    resp = await litellm.acompletion(
        model=model,
        messages=messages,
        stream=True,
        temperature=kwargs.get("temperature", 0.4),
        max_tokens=kwargs.get("max_tokens"),
        # optional retry/failover:
        num_retries=kwargs.get("num_retries", 2),
        fallbacks=kwargs.get("fallbacks"),  # e.g. ["openai/gpt-...", ...] tried in order
        stream_options={"include_usage": True},  # final chunk carries usage (choices == [])
        metadata={"session_id": kwargs.get("session_id")},
    )
    async for chunk in resp:
        if not chunk.choices:  # usage-only chunk at end of stream
            record_usage(chunk.usage)  # prompt_tokens / completion_tokens
            continue
        delta = chunk.choices[0].delta  # type: ignore[union-attr]
        if delta and delta.content is not None:
            yield delta.content
```

- **Provider credentials come from env vars** — never from code. `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, `DEEPSEEK_API_KEY`, etc. LiteLLM reads them automatically. This repo keeps keys in env/keyring (plan.md Part K).
- `litellm.token_counter(messages=..., model=...)` for input token counting; responses carry OpenAI-style `usage.prompt_tokens/completion_tokens/total_tokens` for accounting (plan.md `sessions.tokens`). `litellm.completion_cost(completion_response=resp)` returns USD cost from LiteLLM's price map.

## Proxy config (model_list)

```yaml
# config.yaml for `litellm --config config.yaml` (port 4000)
model_list:
  - model_name: deepseek-r1
    litellm_params:
      model: deepseek/deepseek-reasoner
      api_key: os.environ/DEEPSEEK_API_KEY
  - model_name: claude-sonnet
    litellm_params:
      model: anthropic/claude-sonnet-4-5
      api_key: os.environ/ANTHROPIC_API_KEY
```

Call through the proxy with the OpenAI SDK: `OpenAI(base_url="http://localhost:4000", api_key=...)` then `client.chat.completions.create(model="deepseek-r1", stream=True)`.

## Correctness notes for this repo

- **Retries/fallbacks:** set `num_retries` (LiteLLM's own retry loop — not the provider SDK's `max_retries`); use `fallbacks=["..."]` for provider downtimes (primary `model` is tried first, don't repeat it). For multi-deployment load balancing in-process, `litellm.Router(model_list=[...], num_retries=..., fallbacks=[{"a": ["b"]}])` + `await router.acompletion(...)`. Surface retryable vs fatal through `agent_gateway` errors (don't leak LiteLLM exceptions to `agent_api` — map to typed errors).
- **Budgets per session:** the SDK `completion()` has no per-call cost-cap kwarg — enforce per-run caps in `agent_gateway/registry.py` from `usage` + `completion_cost()`. (Hard budgets via `max_budget`/virtual keys exist on the proxy side.)
- **Ollama/local:** LiteLLM supports `ollama/...` and `hosted_vllm/<model>` (OpenAI-compatible server via `api_base=...`), so the platform stays provider-neutral even offline.
- Wrap LiteLLM behind the `agent_gateway` interface so tests can inject a fake gateway (plan.md Part K testing pyramid); never import `litellm` outside `agent_gateway`.