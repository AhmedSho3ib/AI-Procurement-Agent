# Development Guide

Everything a contributor needs: setup, tests, prompt changes, adding a provider,
troubleshooting the prototype's 401, and the conventions this codebase expects.

---

## 1. Setup

```bash
python -m venv .venv
# Windows (PowerShell)
.venv\Scripts\Activate.ps1
# macOS / Linux
source .venv/bin/activate

pip install -e ".[dev]"       # runtime + test/lint/type dependencies
cp .env.example .env          # then fill in credentials you actually need
```

Run the app:

```bash
python -m uvicorn src.main:create_app --factory --reload --port 8000
# or
python -m src.main
# or
make run
```

`create_app` is a factory, so no import-time side effects: importing `src.main`
never requires credentials and never contacts a provider.

### Dependency declarations

Three files describe dependencies, with one source of truth:

| File | Role |
| --- | --- |
| `pyproject.toml` | **Source of truth**: dependency set and allowed version ranges |
| `requirements.txt` | Exact pins of the runtime set, for environments that need a requirements file (the Docker build uses it for layer caching) |
| `requirements-dev.txt` | `-r requirements.txt` plus the exact pins of pytest / pytest-cov / ruff / mypy |

The rules, enforced by `tests/unit/utils/test_requirements_files.py`:

* no dependency may be added to one file without the others,
* every requirements line must be an exact `==` pin,
* a pin must satisfy the range declared in `pyproject.toml`,
* runtime dependencies must be alphabetical and must not contain index URLs or
  credentials.

So when adding a dependency: update `pyproject.toml`, add the pin to the matching
requirements file, then run `pytest tests/unit/utils/test_requirements_files.py`.
`make req` and `make req-dev` install from the pinned files.

---

## 2. The 60-second loop (no credentials needed)

The whole stack runs offline with mock providers. This is the fastest way to work
on the service, rules, reporting or API:

```bash
# 1. Smoke test end to end (mock providers, in-memory persistence)
python scripts/smoke_test.py --output-dir ai-agent-output

# 2. API with mock providers
APP_LLM_PROVIDER=mock APP_SEARCH_PROVIDER=mock APP_SCRAPER_PROVIDER=mock \
APP_WORKFLOW=sequential APP_PERSISTENCE_BACKEND=memory \
python -m uvicorn src.main:create_app --factory --port 8000

# 3. CLI with mock providers
procure --product "coffee machine for the office" --workflow sequential \
        --llm-provider mock --search-provider mock --scraper-provider mock \
        --persistence memory --output report.html
```

Mock providers are deterministic: the same URL always yields the same synthetic
product, so tests and demos are reproducible. They live in
`src/stores/*/providers/MockProvider.py` and are the reason CI needs no secrets.

---

## 3. Tests

```bash
make test           # python -m pytest
make test-cov       # with coverage
pytest tests/unit/services/test_url_validation.py -v          # one file
pytest -m integration                                          # API tests only
pytest -k deduplication                                        # by name
```

Layout and intent:

| Path | What it covers |
| --- | --- |
| `tests/unit/schemas/` | Contract validation, normalization, bounds |
| `tests/unit/services/test_url_validation.py` | URL rules: validity, suspicious URLs, product-page heuristics, domain allow-list |
| `tests/unit/services/test_product_filtering.py` | Score/price/title filtering, ranking, price statistics |
| `tests/unit/services/test_deduplication.py` | Identity, tracking-variant collapsing, prototype fixtures |
| `tests/unit/services/test_procurement_service.py` | Service orchestration, degradation, persistence, status semantics |
| `tests/unit/services/test_crew_workflow.py` | Crew wiring (4 agents/tasks/tools), output parsing, error classification |
| `tests/unit/services/test_prompt_library.py` | Prompt loading, `$variable` rendering, unresolved-variable failures |
| `tests/unit/stores/` | Factories, mock providers, external adapters (fake SDK clients), retries, error classification, reporting, persistence |
| `tests/unit/controllers/` | Controller delegation and the no-business-logic guard |
| `tests/unit/utils/` | Observability/logging behaviour and dependency-declaration consistency |
| `tests/integration/routes/` | Health, full API flow, validation, error translation |

Rules for new tests:

* **Never require credentials or network.** Inject a provider through the factory
  or replace the provider's `_client` seam with a fake SDK client.
* Test behaviour, not imports. If a test only asserts something imports, delete it.
* Prefer the deterministic workflow for service-level tests; use `FakeWorkflow`
  when you need an exact `WorkflowResult`.
* Herd shared setup into `tests/conftest.py`. `make_settings()` builds hermetic
  settings with `_env_file=None`, so a developer's real `.env` cannot leak in.
* Prototype captures in `tests/fixtures/` come from the original notebook run
  (`notebooks/prototype-output/`) and are used to prove real data still flows.

---

## 4. Changing a prompt

Prompts are Markdown assets, not code:

```text
src/services/procurement_service/prompts/query_agent.md
```

Structure:

```markdown
[ROLE]
Search Queries Recommendation Agent

[GOAL]
...

[BACKSTORY]
...

[TASK]
$company_name is looking to buy $product ...

[EXPECTED_OUTPUT]
A JSON object with a single "queries" key ...
```

`[ROLE]`, `[TASK]` and `[EXPECTED_OUTPUT]` are required. Variables use `$name`
(so literal JSON braces need no escaping). Available variables for every prompt
come from `build_prompt_variables()` in
`src/services/procurement_service/workflow.py`:

`$product`, `$websites`, `$country`, `$language`, `$max_queries`,
`$score_threshold`, `$top_n`, `$company_name`.

Referencing an undefined variable raises `ConfigurationError` at build time — a
test covers this so a typo cannot silently reach a model.

---

## 5. Adding a provider

Example: an OpenAI LLM provider.

1. `src/stores/llm/LLMEnums.py` → add `OPENAI = "openai"`.
2. `src/stores/llm/providers/OpenAIProvider.py` → implement `LLMInterface`
   (`model_name`, `generate`, `generate_structured`, `as_agent_llm`), classify SDK
   errors with `classify_provider_error`, retry with `run_with_retry`.
3. `src/stores/llm/LLMFactory.py` → build and `LLMFactory.register(LLMProvider.OPENAI, builder)`.
4. `src/utils/config.py` → add the credential field (unprefixed alias, `SecretStr`)
   and extend the provider-name literal.
5. `.env.example` → document the new variables.
6. Tests → add a fake-client test next to the Gemini one.

Nothing in `routes/`, `controllers/`, `services/` or `schemas/` changes. That is
the Dependency Inversion guarantee this project is built around.

---

## 6. Troubleshooting: the prototype's 401

The original `agentops.log` from the notebook prototype contained:

```text
ERROR - Error in span Search Queries Recommendation Agent.agent: Error code: 401 -
{'error': {'message': 'unauthorized client detected, contact support ...'},
 'message': 'UNAUTHENTICATED', 'success': False, 'type': 'unauthorized_client_error'}
```

What was actually happening, and how this repository addresses it:

| Finding | Evidence | Resolution in production |
| --- | --- | --- |
| The AgentOps API key was **valid**; this was not an AgentOps auth failure. | `POST https://api.agentops.ai/v3/auth/token` returns `200` with a token and `project_prem_status: free`. Reproduce with `python scripts/check_providers.py --network`. | Nothing to fix in AgentOps, but telemetry is now optional and fail-safe (`APP_AGENTOPS_ENABLED`). |
| The error text is an OpenAI-SDK style `Error code: <status> - <json body>` raised **inside an agent span**, i.e. an upstream model/relay endpoint rejected the credential — not application code. | The body shape (`success`, `type`) belongs to a relay/gateway, and the recorded span was the first agent's LLM call. The project's own Gemini credential works (`56 models visible`). | Upstream failures are now classified (`ProviderAuthError`, permanent, never retried) instead of aborting with an opaque string. Secrets are redacted from error messages. |
| Two telemetry exporters were active at once: `CREWAI_TRACING_ENABLED=true` (CrewAI's own OTLP exporter) plus `agentops.init()`. | Duplicated, identical errors at `02:01:17,203`, `,219`, `,238` for the same span. | `ObservabilityManager` forces `CREWAI_TRACING_ENABLED=false` when it owns the session; `/health` reports whether telemetry is really active. |
| The app could not distinguish "bad credential" from "transient outage", and had no timeouts or retries. | The run aborted on the first agent. | Typed error hierarchy + bounded retries (`APP_MAX_RETRIES`) + per-agent execution limits. |
| The credential may have been exposed during development (notebooks, logs). | The key was stored in `.env` and used from a notebook. | **Rotate any key that has ever been in a notebook, log or commit.** `.env` is git-ignored; CI fails if a credential shape is found in tracked files. |

Diagnostics:

```bash
python scripts/check_providers.py              # offline configuration report
python scripts/check_providers.py --network    # live credential verification
```

When you see this class of error in the future, the questions to ask are: which
endpoint rejected it (the message body style identifies the vendor), does that
credential belong to that endpoint, and was it ever exposed? The application now
answers the first two automatically and never silently pretends telemetry works.

---

## 7. Conventions

* **Type hints everywhere.** `mypy` is configured strict for `src`.
* **No comments that restate code.** Comments explain *why*, especially where a
  non-obvious trade-off was made.
* **Docstrings** on public modules, classes and functions; they describe behaviour
  and failure modes.
* **No broad `except Exception: pass`.** Catch, classify, log with context, then
  either handle deliberately or re-raise a typed error.
* **Deterministic rules stay in code.** If a decision can be made with `if`
  statements, an LLM must not be asked to make it.
* **Dependency direction is not negotiable.** Services depend on store interfaces,
  never on concrete providers or SDKs; routes contain no business logic.
* Line length 110, import order enforced by ruff (`I`).

---

## 8. Quality gates

```bash
make lint          # ruff check src tests scripts
make format        # ruff check --fix + ruff format
make format-check  # ruff format --check (CI equivalent)
make typecheck     # mypy
make test          # pytest
make smoke         # offline end-to-end pipeline
make check-providers NETWORK=1   # live credential verification
```

CI (`.github/workflows/ci.yml`) runs lint, format check, mypy, pytest with a 70%
coverage floor, the offline smoke test, a tracked-file secret scan, and a Docker
build — on Python 3.11, 3.12 and 3.13, with no API keys configured.

---

## 9. Docker

```bash
docker build -t ai-procurement-agent:latest .
docker run --rm -p 8000:8000 --env-file .env ai-procurement-agent:latest

# or
docker compose up --build
```

The image runs as a non-root user, contains no secrets, and keeps SQLite data and
generated reports on volumes (`/app/data`, `/app/ai-agent-output`).

---

## 10. Project layout cheat sheet

```text
src/main.py                     FastAPI factory, middleware, error handlers, lifespan
src/container.py                Composition root (the only place concrete providers meet consumers)
src/cli.py                      CLI reusing the same controller/service

src/routes/                     HTTP contracts only
src/controllers/                Stateless coordination
src/services/procurement_service/
    procurement_service.py      Run orchestration, rules, reporting, persistence
    workflow.py                 Workflow contract + shared context/parsing helpers
    crew.py                     CrewAI workflow (4 agents, 4 tasks)
    sequential.py               Deterministic workflow (CI/tests/offline)
    agents/ tasks/ rules/ prompts/
src/schemas/                    Pydantic contracts
src/stores/{llm,search,scraping,reporting}/   Interface -> Factory -> Provider
src/persistence/models/         SQLite + in-memory repositories
src/utils/                      config, logging, exceptions, retry, observability
templates/                      Jinja2 report templates
tests/                          unit + integration suites with mock providers
scripts/                        smoke test + provider doctor
docs/                           this file, ARCHITECTURE.md, API.md
notebooks/                      the original prototype, kept for reference
```
