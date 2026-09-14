# Architecture

This document explains **how this project is actually built** and why. It is not
generic architecture theory: every section maps to real files in this repository.

---

## 1. The standard we follow

The project implements the layered architecture defined in the repository-level
`AI_PROJECTS_ARCHITECTURE.md`:

```text
src/
├── main.py          # App factory, middleware, error handling, lifespan
├── routes/          # API delivery layer
├── controllers/     # Orchestration layer
├── services/        # Core domain logic (the AI workflow lives here)
├── schemas/         # Pydantic data contracts
├── persistence/     # Data access
├── stores/          # External system adapters (Interface -> Factory -> Provider)
└── utils/           # Configuration, logging, exceptions, infrastructure helpers
```

The request flow is strictly unidirectional:

```text
Client
  -> Route           (HTTP contract, validation, status codes)
  -> Schema          (Pydantic request model)
  -> Controller      (coordination, stateless)
  -> Service         (business workflow)
  -> Persistence / Store   (SQLite repository / external providers)
  -> back up the same path with a response Schema
```

**Layer rules enforced in this codebase**

| Layer | May depend on | Must never |
| --- | --- | --- |
| `routes/` | schemas, controllers, container | contain business logic, SQL, or provider calls |
| `controllers/` | schemas, services | know about providers, SDKs or HTTP |
| `services/` | schemas, stores (interfaces), persistence (repositories), own rules/prompts | know about HTTP, concrete providers, or SDK types |
| `schemas/` | nothing | contain business logic or side effects |
| `persistence/` | schemas, utils | leak SQL or connections upward |
| `stores/` | schemas, utils | be imported by name in services (only interfaces/factories) |
| `utils/` | nothing from the layers above | import services or controllers |

There is exactly one place that wires concrete providers to consumers:
`src/container.py` (the composition root).

---

## 2. Why `src/` is the package

The standard requires the entrypoint at `src/main.py` and the layer directories
directly under `src/`. We therefore ship `src` as the importable package
(`pyproject.toml` → `[tool.setuptools.packages.find] include = ["src*"]`) rather
than introducing a second, differently named package. This keeps a 1:1 mapping
between the mandated blueprint and the tree on disk: no translation layer, no
surprises for reviewers holding the standard next to the code.

Two small additions were required by the standard's own requirements, and are
called out here deliberately:

| Addition | Why it exists |
| --- | --- |
| `src/container.py` | Composition root. Without it, routes would have to import providers, violating the dependency direction. |
| `src/cli.py` | Requirement: a CLI that reuses the controller/service workflow instead of duplicating business logic. |
| `src/utils/retry.py` | Requirement: bounded timeouts/retries with transient-vs-permanent classification. |
| `src/utils/observability.py` | Requirement: AgentOps integration isolated and fail-safe. |
| `src/routes/dependencies.py` | FastAPI DI seams; keeps route modules free of wiring code. |
| `services/procurement_service/sequential.py` | Deterministic workflow for CI/tests with no credentials (see §5). |

---

## 3. Where CrewAI fits

CrewAI is the **AI orchestration technology used by the service layer**, nothing
more:

```text
src/services/procurement_service/
├── procurement_service.py   # ProcurementService: rules, reporting, persistence
├── workflow.py              # ProcurementWorkflow contract (+ shared helpers)
├── crew.py                  # CrewAIProcurementWorkflow (4 agents, 4 tasks)
├── sequential.py            # SequentialProcurementWorkflow (deterministic)
├── agents/                  # one module per agent + its store-backed tools
├── tasks/                   # one module per task, with strict output models
├── rules/                   # deterministic business rules (no LLM)
└── prompts/                 # one reviewable Markdown prompt file per agent
```

* Routes and controllers never import `crewai`.
* Services never import concrete providers: `CrewAIProcurementWorkflow` receives
  `LLMInterface`, `SearchInterface` and `ScraperInterface`.
* Agent tools are the *only* bridge from an agent to an external system, and they
  are constructed inside the agent modules from store interfaces.

An agent is defined once, in two places that belong together:

```text
prompts/query_agent.md   -> [ROLE] [GOAL] [BACKSTORY] [TASK] [EXPECTED_OUTPUT]
agents/query_agent.py    -> builds crewai.Agent from those sections
tasks/query_tasks.py     -> builds the crewai.Task with output_json=SuggestedSearchQueries
```

Prompts live in Markdown (reviewable, diffable, no Python string soup) and use
`$variable` substitution so JSON braces inside prompts need no escaping.
Unresolved variables raise a `ConfigurationError` instead of silently reaching the
model.

---

## 4. Stores: Interface -> Factory -> Provider

Four external systems can be swapped without touching the business workflow:

| Store | Interface | Factory | Providers |
| --- | --- | --- | --- |
| LLM | `LLMInterface` | `LLMFactory` | `GeminiProvider`, `MockProvider` |
| Search | `SearchInterface` | `SearchFactory` | `TavilyProvider`, `MockProvider` |
| Scraping | `ScraperInterface` | `ScraperFactory` | `ScrapeGraphProvider`, `MockProvider` |
| Reporting | `ReportInterface` | `ReportFactory` | `JinjaHTMLProvider`, `MockProvider` |

The factories translate configuration into instances:

```python
search = SearchFactory.create(settings)      # SEARCH_PROVIDER=tavily|mock
```

Adding an `OpenAIProvider` is one enum value plus one `register()` call. No
service, controller, route or schema changes.

Providers own three responsibilities that keep the rest of the code clean:

1. **Vendor mapping** — SDK payloads in, project schemas out.
2. **Retries** — bounded, transient-only, via `utils/retry.py`.
3. **Error classification** — SDK exceptions become typed `ProviderAuthError`,
   `ProviderRateLimitError`, `ProviderTimeoutError`,
   `ProviderUnavailableError` or `ProviderResponseError`, with credentials
   redacted from messages.

That classification is what turned the prototype's opaque
`Error code: 401 - unauthorized client` into a typed, non-retried, log-safe error.

---

## 5. Two workflows, one contract

`ProcurementWorkflow.execute(request) -> WorkflowResult` is the seam:

| Workflow | Driver | Used for |
| --- | --- | --- |
| `CrewAIProcurementWorkflow` (`crew.py`) | Four CrewAI agents, sequential process | Production runs |
| `SequentialProcurementWorkflow` (`sequential.py`) | Deterministic Python control flow | CI, tests, local development without credentials, and graceful degradation |

Both use the **same** stores, schemas, prompts and rules. The service, controller
and API cannot tell them apart, which is why the whole API can be tested offline
without mocking away the layers under test.

Selection is configuration only:

```env
APP_WORKFLOW=crew        # or sequential
```

---

## 6. LLM reasons; code decides

A deliberate split runs through the whole codebase:

| Concern | Owner |
| --- | --- |
| Query ideation, product ranking, narrative report content | LLM (`stores/llm`) |
| URL validity, allowed domains, suspicious/non-product detection | `rules/url_validation.py` |
| Score thresholds, invalid prices/titles, price statistics | `rules/product_filtering.py` |
| Duplicate collapsing (normalised URL + product identifiers) | `rules/deduplication.py` |
| Rendering the final document | `stores/reporting` + `templates/` |

The service re-applies the deterministic rules **after** every workflow. If a model
returns a login page, a zero price, a duplicate offer or an off-marketplace URL, it
is filtered by code and the run records *why* in `warnings`. Guarantees therefore
do not depend on model behaviour.

---

## 7. Reporting

```text
report agent (crew)  ─┐
                     ├─> ProcurementReport (content only: 8 sections)
report step (seq.)   ─┘              │
                                     v
                          ReportInterface.render()
                                     │
                 JinjaHTMLProvider ──┴── templates/procurement_report.html
                                          templates/procurement_report.md.jinja
```

* The LLM never writes HTML; the prompt forbids it explicitly.
* `ReportContext` carries only data (products, price statistics, queries, warnings).
* HTML/Markdown/JSON are supported; JSON exists for machine consumption.
* Jinja autoescaping is on, so hostile scraped titles cannot inject markup (there
  is a test for it).

The prototype's eight sections, Bootstrap styling and price table are preserved;
the giant HTML string moved out of the agent into a template.

---

## 8. Persistence

`persistence/models/procurement.py` holds one table and two implementations of one
contract:

* `SQLiteProcurementRepository` (default) — stdlib `sqlite3`, no ORM, no migrations
  framework, no connection pool to operate.
* `InMemoryProcurementRepository` — used by tests and `APP_PERSISTENCE_BACKEND=memory`.

Why persist at all: a procurement run is an auditable business event, and
`GET /api/v1/procurement/{request_id}` makes the stored report retrievable. Why
*only* this: the product needs a trail, not a distributed data platform.

A persistence failure is logged and surfaced as a warning — it never discards an
already-computed result.

---

## 9. Configuration, secrets and observability

* All environment access is centralised in `src/utils/config.py` (pydantic-settings).
* Behaviour settings are namespaced `APP_*` (`APP_DEBUG`, `APP_PORT`, …). This is
  not cosmetic: during development this project hit a real collision with an
  ambient host variable `DEBUG=WARN` that broke startup. Provider credentials keep
  their canonical names (`GOOGLE_API_KEY`, `TVLY_API_KEY`, …) so existing tooling
  and the original `.env` keep working.
* Secrets are `SecretStr`. `Settings.safe_summary()` and `/health` expose booleans
  only — a test asserts that no credential shape appears in the health payload.
* Startup fails fast on *inconsistent* configuration (for example
  `LLM_PROVIDER=gemini` with no key), which is operator error, not a runtime
  condition to discover on the first request.
* AgentOps lives only in `src/utils/observability.py`, is disabled by default, and
  is fail-safe: initialisation or export failures are logged and swallowed, and the
  manager reports honestly whether telemetry is active (`/health` →
  `observability.active`). When it is enabled it also forces
  `CREWAI_TRACING_ENABLED=false`, because the prototype's duplicated spans came
  from CrewAI's own OTLP exporter and AgentOps both exporting to the same endpoint.

---

## 10. Errors and reliability

`src/utils/exceptions.py` defines the hierarchy; every error carries a stable
`code`, an HTTP status, structured (secret-free) context, and a `retryable` flag.

```text
AppError
├── ConfigurationError          500  permanent
├── InvalidInputError           422  permanent
├── NotFoundError               404  permanent
├── PersistenceError            500  permanent
├── ProcurementError            502
│   ├── QueryGenerationError
│   ├── SearchProviderError
│   ├── ScrapingError
│   └── ReportGenerationError
└── ExternalServiceError        502
    ├── ProviderAuthError       502  never retried
    ├── ProviderRateLimitError  429  retried (bounded)
    ├── ProviderTimeoutError    504  retried (bounded)
    ├── ProviderUnavailableError 503 retried (bounded)
    └── ProviderResponseError   502  never retried
```

* Retries are bounded by `APP_MAX_RETRIES` with linear backoff; validation and auth
  errors are never retried.
* `main.py` translates `AppError` into a stable JSON body
  (`{"error": {"code", "message", "details", "request_id"}}`) and unexpected
  exceptions into a generic 500 that never leaks internals.
* Partial failures degrade instead of failing: failed queries, failed page scrapes,
  a failed ranking call, a failed render and a failed write all appear in
  `response.warnings`.

---

## 11. Concurrency model

Everything below the route layer is synchronous. FastAPI executes `def` path
operations in a worker threadpool, which is exactly right for blocking SDK calls
(HTTP clients, CrewAI kickoff) and keeps the code free of accidental
sync/async mixing. CrewAI's own `kickoff` is blocking; wrapping it in `async def`
would block the event loop unless every layer were converted, for no benefit at
this scope.

---

## 12. Dependency graph (no cycles)

```text
main ──> routes ──> controllers ──> services ──> stores (interfaces)
  │            │                        │             ↑
  │            └──> schemas <───────────┴─────────────┘
  ├──> container ──> stores (factories/providers)
  │            └──> persistence
  └──> utils (config, logging, exceptions, retry, observability)
```

`services` imports only `stores.<system>` package facades (which expose the
interface and factory) — never a concrete provider module, and never `crewai`
outside the workflow/agent/task modules.

---

## 13. What we deliberately did **not** build

Kafka, Celery, Redis, Kubernetes, microservices, an event bus, an ORM layer, a
migrations framework, a caching tier and a separate model-serving service. None of
them solve a problem this product currently has; each would add an operational
surface without adding capability. The result is a **modular monolith**: one
container, one SQLite file, clear seams where a component could be extracted later
(the store interfaces already mark those seams).
