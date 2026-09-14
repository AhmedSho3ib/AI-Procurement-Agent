"""Factory resolving the configured scraping provider."""

from __future__ import annotations

from collections.abc import Callable
from typing import ClassVar

from src.stores.scraping.providers.MockProvider import MockProvider as MockScraperProvider
from src.stores.scraping.providers.ScrapeGraphProvider import ScrapeGraphProvider
from src.stores.scraping.ScraperEnums import ScraperProvider
from src.stores.scraping.ScraperInterface import ScraperInterface
from src.utils.config import Settings
from src.utils.exceptions import ConfigurationError
from src.utils.logging import get_logger

__all__ = ["ScraperFactory"]

logger = get_logger(__name__)


class ScraperFactory:
    """Create the concrete scraping provider selected by configuration."""

    _registry: ClassVar[dict[ScraperProvider, Callable[[Settings], ScraperInterface]]] = {}

    @classmethod
    def register(cls, provider: ScraperProvider, builder: Callable[[Settings], ScraperInterface]) -> None:
        cls._registry[provider] = builder

    @classmethod
    def supported_providers(cls) -> list[str]:
        return sorted(provider.value for provider in cls._registry)

    @classmethod
    def create(
        cls,
        settings: Settings,
        provider: str | ScraperProvider | None = None,
    ) -> ScraperInterface:
        resolved = cls._resolve(provider if provider is not None else settings.scraper_provider)
        builder = cls._registry.get(resolved)
        if builder is None:  # pragma: no cover - defensive
            raise ConfigurationError(
                "No builder registered for scraping provider",
                context={"provider": resolved.value, "supported": cls.supported_providers()},
            )
        instance = builder(settings)
        logger.debug("scraper_provider_created", extra=instance.describe())
        return instance

    @staticmethod
    def _resolve(provider: str | ScraperProvider) -> ScraperProvider:
        if isinstance(provider, ScraperProvider):
            return provider
        try:
            return ScraperProvider(str(provider).strip().lower())
        except ValueError as error:
            raise ConfigurationError(
                "Unsupported scraping provider",
                context={"provider": str(provider), "supported": [p.value for p in ScraperProvider]},
                cause=error,
            ) from error


def _build_scrapegraph(settings: Settings) -> ScraperInterface:
    return ScrapeGraphProvider(
        api_key=settings.scrapegraph_api_key,
        timeout_seconds=max(settings.request_timeout_seconds, 30.0),
        max_retries=settings.max_retries,
        retry_backoff_seconds=settings.retry_backoff_seconds,
    )


def _build_mock(settings: Settings) -> ScraperInterface:
    return MockScraperProvider()


ScraperFactory.register(ScraperProvider.SCRAPEGRAPH, _build_scrapegraph)
ScraperFactory.register(ScraperProvider.MOCK, _build_mock)
