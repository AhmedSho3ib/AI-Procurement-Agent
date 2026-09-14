# API Reference

Base URL: `http://localhost:8000` (configurable via `APP_HOST` / `APP_PORT`).
Interactive docs: `/docs` (Swagger UI) and `/redoc`.
Machine readable contract: `/openapi.json`.

All endpoints are JSON. All errors share one shape:

```json
{
  "error": {
    "code": "provider_auth_error",
    "message": "gemini rejected the credentials during crew.kickoff",
    "details": { "provider": "gemini", "operation": "crew.kickoff" },
    "request_id": "6f1c2b0e-2e0e-4c66-9c2a-0f0a2f0c9a10"
  }
}
```

`request_id` is echoed from the `x-request-id` request header when supplied, and
generated otherwise. It is also returned as the `x-request-id` response header,
alongside `x-process-time-ms`.

---

## `GET /health`

Liveness and configuration probe. Deliberately does **not** call any external
provider, so a third-party outage cannot make this service look unhealthy.

```bash
curl http://localhost:8000/health
```

```json
{
  "status": "ok",
  "app_name": "AI Procurement Agent",
  "version": "1.0.0",
  "environment": "local",
  "timestamp": "2026-09-14T16:00:00+00:00",
  "workflow": "crew",
  "providers": {
    "llm": { "provider": "gemini", "model": "gemini-2.0-flash" },
    "search": { "provider": "tavily", "search_depth": "basic", "timeout_seconds": 30.0, "include_domains": [] },
    "scraper": { "provider": "scrapegraph", "timeout_seconds": 60.0 },
    "report": { "provider": "jinja", "template_dir": "templates", "templates": ["procurement_report.html"] },
    "persistence": { "backend": "sqlite" }
  },
  "observability": { "provider": "agentops", "enabled": false, "active": false, "last_error": null },
  "configuration": {
    "providers": { "llm": "gemini", "search": "tavily", "scraper": "scrapegraph", "report": "jinja" },
    "credentials_configured": { "google": true, "tavily": true, "scrapegraph": true, "agentops": false },
    "workflow": "crew",
    "persistence_backend": "sqlite"
  }
}
```

Credentials are reported as booleans only. No key material is ever returned,
logged or traced.

---

## `POST /api/v1/procurement`

Runs the procurement workflow: generate queries → search → extract products →
render the report.

### Request body

| Field | Type | Default | Constraints | Notes |
| --- | --- | --- | --- | --- |
| `product` | string | — | 3–300 chars | What the company wants to buy. Required. |
| `websites` | string[] | — | 1–20 entries | Target marketplaces. Domains, URLs or display names. Required. |
| `country` | string | `"Egypt"` | 2–80 chars | Target market. |
| `language` | string | `"English"` | 2–40 chars | Language of the generated queries and report. |
| `max_queries` | integer | `10` | 1–20 | Maximum number of search queries. |
| `score_threshold` | number | `0.10` | 0.0–1.0 | Minimum search relevance score to keep a result. |
| `top_n` | integer | `10` | 1–50 | Products kept for the report. |
| `company_name` | string | `"Rankyx"` | 1–120 chars | Used in the report. |
| `report_format` | enum | `"html"` | `html` \| `markdown` \| `json` | Rendered report format. |
| `generate_report` | boolean | `true` | — | Set `false` to skip report generation. |

Field naming maps back to the original prototype inputs: `product` →
`product_name`, `websites` → `websites_list`, `country` → `country_name`,
`max_queries` → `no_keywords`, `score_threshold` → `score_th`, `top_n` →
`top_recommendations_no`.

```bash
curl -X POST http://localhost:8000/api/v1/procurement \
  -H "Content-Type: application/json" \
  -d '{
    "product": "coffee machine for the office",
    "websites": ["www.amazon.eg", "www.jumia.com.eg", "www.noon.com/egypt-en"],
    "country": "Egypt",
    "language": "English",
    "max_queries": 10,
    "score_threshold": 0.10,
    "top_n": 10
  }'
```

### Response `200 OK`

```json
{
  "request_id": "0e2f1f0a-9f4d-4f7b-b1c9-6f8d2b6a1c33",
  "status": "completed",
  "request": {
    "product": "coffee machine for the office",
    "websites": ["www.amazon.eg", "www.jumia.com.eg", "www.noon.com/egypt-en"],
    "country": "Egypt",
    "language": "English",
    "max_queries": 10,
    "score_threshold": 0.1,
    "top_n": 10,
    "company_name": "Rankyx",
    "report_format": "html",
    "generate_report": true
  },
  "queries": [
    "DeLonghi Magnifica S automatic coffee machine site:amazon.eg",
    "Nespresso Vertuo Next coffee machine site:noon.com/egypt-en"
  ],
  "search_results": [
    {
      "title": "De'Longhi Magnifica S Automatic Coffee Machine, ECAM 22.110.B, Black",
      "url": "https://www.amazon.eg/DeLonghi-Magnifica-Automatic-Coffee-Machine/dp/B00400OMU0",
      "content": "De'Longhi Magnifica S ... Price: EGP 24,999.00.",
      "score": 0.98,
      "search_query": "DeLonghi Magnifica S automatic coffee machine site:amazon.eg"
    }
  ],
  "products": [
    {
      "page_url": "https://www.amazon.eg/DeLonghi-Magnifica-Automatic-Coffee-Machine/dp/B00400OMU0",
      "product_title": "De'Longhi Magnifica S Automatic Coffee Machine, ECAM 22.110.B, Black",
      "product_image_url": "https://m.media-amazon.com/images/I/61+DeLonghi-Magnifica-S.jpg",
      "product_url": "https://www.amazon.eg/DeLonghi-Magnifica-Automatic-Coffee-Machine/dp/B00400OMU0",
      "product_current_price": 24999.0,
      "product_original_price": null,
      "product_discount_percentage": null,
      "product_currency": "EGP",
      "product_specs": [
        { "specification_name": "Type", "specification_value": "Automatic" },
        { "specification_name": "Grinder", "specification_value": "Integrated" }
      ],
      "agent_recommendation_rank": 5,
      "agent_recommendation_notes": ["Best value for an office environment."]
    }
  ],
  "price_summary": {
    "product_count": 10,
    "currency": "EGP",
    "min_price": 1450.0,
    "max_price": 65000.0,
    "average_price": 12840.3,
    "median_price": 3100.0,
    "cheapest_product_title": "Fresh Coffee Maker, 1000 Watt, 1.2 Liter",
    "cheapest_product_url": "https://www.jumia.com.eg/fresh-coffee-maker-1000-watt-1.2-liter.html",
    "most_expensive_product_title": "Miele CM 5310 Silence Coffee Machine, Obsidian Black"
  },
  "report": {
    "format": "html",
    "content": "<!DOCTYPE html> ...",
    "generated_at": "2026-09-14T16:00:05.123456+00:00"
  },
  "warnings": [
    "Filtered 4 of 30 search results (low score, invalid URL or non-product page)."
  ],
  "generated_at": "2026-09-14T16:00:05.123456+00:00",
  "duration_ms": 41857,
  "metadata": {
    "workflow": "crew",
    "providers": { "llm": { "provider": "gemini", "model": "gemini-2.0-flash" }, "search": { "provider": "tavily" } },
    "counts": { "search_results_kept": 26, "products_kept": 10 },
    "workflow_metadata": { "token_usage": { "total_tokens": 15432 } },
    "observability": { "provider": "agentops", "enabled": false, "active": false, "last_error": null }
  }
}
```

### `status` semantics

| Value | Meaning |
| --- | --- |
| `completed` | Products were extracted and (when requested) the report rendered. |
| `partial` | Something degraded: no report content, a render failure, or no products while a report exists. Inspect `warnings`. |
| `failed` | Nothing usable was produced. `warnings` explains why. |

### Status codes

| Code | `error.code` | When |
| --- | --- | --- |
| 200 | — | Run completed (including `partial` runs — read `status` and `warnings`). |
| 422 | — (FastAPI `detail`) | Request failed schema validation. |
| 429 | `provider_rate_limited` | A provider throttled the request. |
| 500 | `configuration_error`, `persistence_error`, `internal_error` | Misconfiguration or an unexpected failure. |
| 502 | `provider_auth_error`, `provider_response_invalid`, `search_provider_failed`, `scraping_failed`, `query_generation_failed`, `report_generation_failed` | A provider rejected the request or returned an unusable payload. |
| 503 | `provider_unavailable` | Provider temporarily unreachable. |
| 504 | `provider_timeout` | Provider did not answer in time. |

Errors never include stack traces, SQL, file paths or credentials.

---

## `GET /api/v1/procurement/{request_id}`

Returns a persisted run, including its rendered report. Useful for retrieving a
report without re-running the workflow.

```bash
curl http://localhost:8000/api/v1/procurement/0e2f1f0a-9f4d-4f7b-b1c9-6f8d2b6a1c33
```

Response: the same `ProcurementResponse` schema, or `404` with
`error.code = "not_found"` when the id is unknown.

---

## CLI equivalent

The CLI uses the same controller and service as the HTTP API:

```bash
# Mock providers: no credentials, no network
procure --product "coffee machine for the office" \
        --workflow sequential --llm-provider mock --search-provider mock \
        --scraper-provider mock --persistence memory --output report.html

# Full response as JSON (same schema as the API)
procure --product "coffee machine for the office" --json

# Markdown report written to disk
procure --product "office chair" --format markdown --output chair.md
```

Exit codes: `0` success, `1` application/provider error, `2` configuration error,
`130` interrupted.
