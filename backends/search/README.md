# backends/search/

Web search providers behind `src/auoreo/backends/search.py` (`SearchBackend.search`,
optional `fetch` reader). Unlike other kinds, `web_search` walks an ordered chain
(`settings.i9.search.order`, default `exa, parallel, brave, tavily, jina, firecrawl, searxng`):
providers without their key (or URL) are skipped, the first one that answers wins, and the
answer is cached for 24 h (`<home>/search_cache.db`, canonical-URL + title dedup).

| backend | key | notes |
|---|---|---|
| `exa` | `EXA_API_KEY` | `maxAgeHours`, `text.maxCharacters`, `costDollars` to usage, 402 = BUDGET |
| `parallel` | `PARALLEL_API_KEY` | `advanced/basic/turbo`, objective + 2-3 keyword fan-out |
| `brave` | `BRAVE_API_KEY` | `/web/search` default, `/llm/context` via `i9.search.brave_endpoint` |
| `tavily` | `TAVILY_API_KEY` | basic, 8 results, credits to usage, 432/433 = BUDGET |
| `jina` | `JINA_API_KEY` | search + Reader (web_fetch fallback) |
| `firecrawl` | `FIRECRAWL_API_KEY` | search + scrape (web_fetch fallback), self-host URL |
| `searxng` | none (`settings.searxng_url`) | local instance, reachable through the egress proxy |

All HTTP goes through `auoreo.tools.net.egress_client` (the loopback egress proxy for this
`net: proxy` tool). `exa` carries `default: true` only so doctor shows one `backend:search`
row; the chain order is what `web_search` uses. Contract tests:
`tests/unit/test_search_providers_i9.py` (pytest_httpx).
