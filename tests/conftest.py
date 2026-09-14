"""Shared pytest fixtures.

The entire suite runs **offline**: mock providers, in-memory persistence and
``_env_file=None`` so a developer's real ``.env`` (and their API keys) can never
influence a test result.

Two guards enforce that hermeticity (see ``docs/DEVELOPMENT.md`` §3):

1. ``dotenv.load_dotenv`` is neutralised. CrewAI (through litellm) calls
   ``load_dotenv()`` at import time, which would otherwise inject the real ``.env``
   credentials into ``os.environ`` mid-session and make tests order dependent.
2. An autouse fixture removes provider credentials from ``os.environ`` before every
   test, as defence in depth.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

# --- Guard 1: stop third-party libraries from loading the developer's .env -----
try:
    import dotenv

    dotenv.load_dotenv = lambda *_args, **_kwargs: False  # type: ignore[assignment]
    dotenv.load_dotenv.__doc__ = "Disabled in tests: tests must be hermetic."
except Exception:  # noqa: BLE001 - the guard is best effort and must never fail collection
    pass

# Force a hermetic environment before any application module is imported. These
# override whatever is in `.env`, guaranteeing no test can reach a real provider.
os.environ.update(
    {
        "APP_ENVIRONMENT": "test",
        "APP_LLM_PROVIDER": "mock",
        "APP_SEARCH_PROVIDER": "mock",
        "APP_SCRAPER_PROVIDER": "mock",
        "APP_REPORT_PROVIDER": "jinja",
        "APP_WORKFLOW": "sequential",
        "APP_PERSISTENCE_BACKEND": "memory",
        "APP_AGENTOPS_ENABLED": "false",
        "APP_LOG_LEVEL": "WARNING",
        "APP_REPORT_OUTPUT_DIR": "",
        "CREWAI_TRACING_ENABLED": "false",
    }
)

from src.container import AppContainer, build_container
from src.main import create_app
from src.schemas.procurement import ProcurementRequest
from src.schemas.products import Product, ProductSpecification, ScrapedProduct
from src.schemas.search import SearchResult
from src.services.procurement_service.prompts import PromptLibrary
from src.stores.llm.providers.MockProvider import MockProvider as MockLLM
from src.stores.reporting.providers.JinjaHTMLProvider import JinjaHTMLProvider
from src.stores.reporting.providers.MockProvider import MockProvider as MockReport
from src.stores.scraping.providers.MockProvider import MockProvider as MockScraper
from src.stores.search.providers.MockProvider import MockProvider as MockSearch
from src.utils.config import Settings

FIXTURES_DIR = Path(__file__).parent / "fixtures"
REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = REPO_ROOT / "templates"

#: Credentials that must never be visible to a test, whatever the host environment.
CREDENTIAL_ENV_VARS = (
    "GOOGLE_API_KEY",
    "AGENTOPS_API_KEY",
    "TVLY_API_KEY",
    "TAVILY_API_KEY",
    "SCRAPEGRAPH_API_KEY",
)


@pytest.fixture(autouse=True)
def _sanitised_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guard 2: no real credential is ever visible to a test."""
    for name in CREDENTIAL_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def make_settings(**overrides: Any) -> Settings:
    """Build hermetic settings for tests (never reads ``.env``)."""
    base: dict[str, Any] = {
        "environment": "test",
        "debug": False,
        "log_level": "WARNING",
        "llm_provider": "mock",
        "search_provider": "mock",
        "scraper_provider": "mock",
        "report_provider": "jinja",
        "workflow": "sequential",
        "persistence_backend": "memory",
        "report_output_dir": "",
        "report_template_dir": str(TEMPLATES_DIR),
        "agentops_enabled": False,
        "max_retries": 2,
        "retry_backoff_seconds": 0.0,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def mock_llm() -> MockLLM:
    return MockLLM()


@pytest.fixture
def mock_search() -> MockSearch:
    return MockSearch()


@pytest.fixture
def mock_scraper() -> MockScraper:
    return MockScraper()


@pytest.fixture
def mock_report() -> MockReport:
    return MockReport()


@pytest.fixture
def jinja_report() -> JinjaHTMLProvider:
    return JinjaHTMLProvider(TEMPLATES_DIR)


@pytest.fixture
def prompt_library() -> PromptLibrary:
    return PromptLibrary(REPO_ROOT / "src" / "services" / "procurement_service" / "prompts")


@pytest.fixture
def container(settings: Settings) -> Iterator[AppContainer]:
    built = build_container(settings)
    yield built
    built.shutdown()


@pytest.fixture
def client(settings: Settings) -> Iterator[Any]:
    from fastapi.testclient import TestClient

    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def procurement_request() -> ProcurementRequest:
    return ProcurementRequest(
        product="coffee machine for the office",
        websites=["www.amazon.eg", "www.jumia.com.eg", "www.noon.com/egypt-en"],
        country="Egypt",
        language="English",
        max_queries=4,
        score_threshold=0.10,
        top_n=4,
    )


# --------------------------------------------------------------------------- #
# Fixture data (captured from the original prototype's run)
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="session")
def prototype_queries() -> dict[str, Any]:
    return json.loads((FIXTURES_DIR / "prototype_suggested_queries.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def prototype_search_results() -> dict[str, Any]:
    return json.loads((FIXTURES_DIR / "prototype_search_results.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def prototype_products() -> dict[str, Any]:
    return json.loads((FIXTURES_DIR / "prototype_products.json").read_text(encoding="utf-8"))


@pytest.fixture
def search_result() -> SearchResult:
    return SearchResult(
        title="De'Longhi Magnifica S",
        url="https://www.amazon.eg/DeLonghi-Magnifica/dp/B00400OMU0",
        content="Automatic coffee machine",
        score=0.98,
        search_query="coffee machine site:amazon.eg",
    )


@pytest.fixture
def scraped_product() -> ScrapedProduct:
    return ScrapedProduct(
        page_url="https://www.amazon.eg/DeLonghi-Magnifica/dp/B00400OMU0",
        product_title="De'Longhi Magnifica S Automatic Coffee Machine",
        product_image_url="https://m.media-amazon.com/images/I/de-longhi.jpg",
        product_url="https://www.amazon.eg/DeLonghi-Magnifica/dp/B00400OMU0",
        product_current_price=24999.0,
        product_original_price=29999.0,
        product_discount_percentage=16.7,
        product_currency="EGP",
        product_specs=[ProductSpecification(specification_name="Type", specification_value="Automatic")],
    )


@pytest.fixture
def product(scraped_product: ScrapedProduct) -> Product:
    return Product(
        **scraped_product.model_dump(),
        agent_recommendation_rank=5,
        agent_recommendation_notes=["Best value for an office environment."],
    )
