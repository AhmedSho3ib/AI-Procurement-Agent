"""Factory tests: configuration-driven provider resolution.

These tests prove the Interface -> Factory -> Provider pattern actually works:
swapping an implementation is a configuration value, not a code change.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.stores.llm import LLMFactory, LLMProvider
from src.stores.llm.providers.GeminiProvider import GeminiProvider
from src.stores.llm.providers.MockProvider import MockProvider as MockLLMProvider
from src.stores.reporting import ReportFactory, ReportProvider
from src.stores.reporting.providers.JinjaHTMLProvider import JinjaHTMLProvider
from src.stores.reporting.providers.MockProvider import MockProvider as MockReportProvider
from src.stores.scraping import ScraperFactory, ScraperProvider
from src.stores.scraping.providers.MockProvider import MockProvider as MockScraperProvider
from src.stores.scraping.providers.ScrapeGraphProvider import ScrapeGraphProvider
from src.stores.search import SearchFactory, SearchProvider
from src.stores.search.providers.MockProvider import MockProvider as MockSearchProvider
from src.stores.search.providers.TavilyProvider import TavilyProvider
from src.utils.config import Settings
from src.utils.exceptions import ConfigurationError
from tests.conftest import TEMPLATES_DIR, make_settings


class TestLLMFactory:
    def test_creates_the_mock_provider_from_settings(self, settings: Settings) -> None:
        provider = LLMFactory.create(settings)
        assert isinstance(provider, MockLLMProvider)
        assert provider.provider is LLMProvider.MOCK

    def test_creates_gemini_when_configured_with_a_key(self) -> None:
        provider = LLMFactory.create(
            make_settings(llm_provider="gemini", google_api_key="test-key", google_model="gemini-2.0-flash")
        )
        assert isinstance(provider, GeminiProvider)
        assert provider.model_name == "gemini-2.0-flash"

    def test_explicit_provider_argument_overrides_settings(self) -> None:
        # gemini without a key must fail, which proves the argument took effect.
        with pytest.raises(ConfigurationError):
            LLMFactory.create(make_settings(llm_provider="mock"), provider="gemini")

    def test_unknown_provider_raises_configuration_error(self, settings: Settings) -> None:
        with pytest.raises(ConfigurationError) as error:
            LLMFactory.create(settings, provider="some-other-vendor")
        assert "supported" in error.value.context

    def test_providers_are_discoverable(self) -> None:
        assert LLMFactory.supported_providers() == ["gemini", "mock"]


class TestSearchFactory:
    def test_creates_mock_provider(self, settings: Settings) -> None:
        assert isinstance(SearchFactory.create(settings), MockSearchProvider)

    def test_creates_tavily_provider_with_domains(self) -> None:
        provider = SearchFactory.create(
            make_settings(search_provider="tavily", tvly_api_key="tvly-test"),
            include_domains=["www.amazon.eg"],
        )
        assert isinstance(provider, TavilyProvider)
        assert provider.describe()["include_domains"] == ["www.amazon.eg"]

    def test_tavily_without_key_is_rejected_by_settings(self) -> None:
        # Settings fail fast: an inconsistent configuration must not reach runtime.
        with pytest.raises(ValidationError):
            make_settings(search_provider="tavily")

    def test_tavily_provider_itself_requires_a_key(self) -> None:
        with pytest.raises(ConfigurationError):
            TavilyProvider(api_key=None)

    def test_unknown_provider_raises(self, settings: Settings) -> None:
        with pytest.raises(ConfigurationError):
            SearchFactory.create(settings, provider="another-search-engine")


class TestScraperFactory:
    def test_creates_mock_provider(self, settings: Settings) -> None:
        assert isinstance(ScraperFactory.create(settings), MockScraperProvider)

    def test_creates_scrapegraph_provider(self) -> None:
        provider = ScraperFactory.create(
            make_settings(scraper_provider="scrapegraph", scrapegraph_api_key="sgai-test")
        )
        assert isinstance(provider, ScrapeGraphProvider)

    def test_scrapegraph_provider_itself_requires_a_key(self) -> None:
        with pytest.raises(ConfigurationError):
            ScrapeGraphProvider(api_key="   ")

    def test_scrapegraph_without_key_is_rejected_by_settings(self) -> None:
        with pytest.raises(ValidationError):
            make_settings(scraper_provider="scrapegraph")


class TestReportFactory:
    def test_creates_jinja_provider_with_templates(self, settings: Settings) -> None:
        provider = ReportFactory.create(settings, template_dir=TEMPLATES_DIR)
        assert isinstance(provider, JinjaHTMLProvider)
        assert "procurement_report.html" in provider.describe()["templates"]

    def test_creates_mock_provider(self, settings: Settings) -> None:
        provider = ReportFactory.create(settings, provider=ReportProvider.MOCK)
        assert isinstance(provider, MockReportProvider)

    def test_unknown_provider_raises(self, settings: Settings) -> None:
        with pytest.raises(ConfigurationError):
            ReportFactory.create(settings, provider="pdf-writer")


class TestProviderNameConsistency:
    """Guard against the configured literals drifting from the store enums."""

    @pytest.mark.parametrize(
        ("enum", "expected"),
        [
            (LLMProvider, {"gemini", "mock"}),
            (SearchProvider, {"tavily", "mock"}),
            (ScraperProvider, {"scrapegraph", "mock"}),
            (ReportProvider, {"jinja", "mock"}),
        ],
    )
    def test_enum_values_are_stable(self, enum: type, expected: set[str]) -> None:
        assert {member.value for member in enum} == expected

    def test_settings_accept_every_registered_llm_provider(self) -> None:
        for provider in LLMProvider:
            credentials = {"google_api_key": "test-key"} if provider is LLMProvider.GEMINI else {}
            assert make_settings(llm_provider=provider.value, **credentials).llm_provider == provider.value

    def test_settings_require_credentials_for_real_providers(self) -> None:
        with pytest.raises(ValidationError):
            make_settings(llm_provider="gemini")

    def test_settings_reject_an_unregistered_llm_provider(self) -> None:
        with pytest.raises(ValidationError):
            make_settings(llm_provider="unsupported")
