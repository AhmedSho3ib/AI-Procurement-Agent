# Notebooks

## `CrewAI.ipynb` — the original prototype

This notebook is the **original working prototype** of the AI Procurement Agent. It
is kept as reference (and as the source of the fixtures used by the test suite). It
is **not** part of the production application and nothing in `src/` executes it.

The production implementation now lives in [`../src`](../src):

| Prototype cell | Production location |
| --- | --- |
| `load_dotenv`, env validation, `agentops.init` | `src/utils/config.py`, `src/utils/observability.py` |
| `basic_llm`, `search_client`, `scrape_client` | `src/stores/{llm,search,scraping}/providers/` behind interfaces + factories |
| `SuggestedSearchQueries` + query agent/task | `src/schemas/search.py`, `src/services/procurement_service/agents/query_agent.py`, `tasks/query_tasks.py`, `prompts/query_agent.md` |
| `SignleSearchResult` / `AllSearchResults` + search agent/task | `src/schemas/search.py`, `agents/search_agent.py`, `tasks/search_tasks.py` |
| `search_engine_tool` (Tavily) | `src/stores/search/providers/TavilyProvider.py` + the agent's `search_products` tool |
| `SingleExtractedProduct` / `AllExtractedProducts` + scraping agent/task | `src/schemas/products.py`, `agents/extraction_agent.py`, `tasks/extraction_tasks.py` |
| `web_scraping_tool` (ScrapeGraph) | `src/stores/scraping/providers/ScrapeGraphProvider.py` + the agent's `scrape_product_page` tool |
| Report agent producing a giant HTML string | `agents/report_agent.py` produces `ProcurementReport` **content**; `templates/procurement_report.html` renders it |
| `Crew(...)` + `kickoff_async(inputs=...)` | `src/services/procurement_service/crew.py` (driven by the API/CLI) |
| Output files `ai-agent-output/step_*.json` | `APP_REPORT_OUTPUT_DIR` step artifacts (`prototype-output/` holds the originals) |

What changed and why:

* provider credentials are no longer read with `os.environ` inside notebook cells;
  they are validated centrally and never logged;
* URLs, prices, titles and duplicates are filtered by deterministic rules instead of
  being entrusted to a model;
* the API, controllers, services, stores and tests are separated so each can be
  changed and verified independently;
* the prototype's `401 unauthorized client` failure mode is now a typed, classified,
  non-retried error (see `docs/DEVELOPMENT.md` §6).

## `prototype-output/`

Raw artifacts captured from a prototype run, kept for traceability and reused as
test fixtures (copies live in `tests/fixtures/`):

* `step_1_suggested_search_queries.json`
* `step_2_search_results.json`
* `step_3_search_results.json`
* `step_4_procurement_report.html`

To run the production pipeline instead:

```bash
python scripts/smoke_test.py --output-dir ai-agent-output
# or
procure --product "coffee machine for the office" --workflow crew
```
