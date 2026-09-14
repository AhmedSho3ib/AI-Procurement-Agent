"""Factory resolving the configured search provider."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import ClassVar

from src.stores.search.providers.MockProvider import MockProvider as MockSearchProvider
from src.stores.search.providers.TavilyProvider import TavilyProvider
from src.stores.search.SearchEnums import SearchProvider
from src.stores.search.SearchInterface import SearchInterface
from src.utils.config import Settings
from src.utils.exceptions import ConfigurationError
from src.utils.logging import get_logger

__all__ = ["SearchFactory"]

logger = get_logger(__name__)


class SearchFactory:
    """Create the concrete search provider selected by configuration."""

    _registry: ClassVar[
        dict[SearchProvider, Callable[[Settings, Sequence[str] | None], SearchInterface]]
    ] = {}

    @classmethod
    def register(
        cls,
        provider: SearchProvider,
        builder: Callable[[Settings, Sequence[str] | None], SearchInterface],
    ) -> None:
        cls._registry[provider] = builder

    @classmethod
    def supported_providers(cls) -> list[str]:
        return sorted(provider.value for provider in cls._registry)

    @classmethod
    def create(
        cls,
        settings: Settings,
        provider: str | SearchProvider | None = None,
        *,
        include_domains: Sequence[str] | None = None,
    ) -> SearchInterface:
        resolved = cls._resolve(provider if provider is not None else settings.search_provider)
        builder = cls._registry.get(resolved)
        if builder is None:  # pragma: no cover - defensive
            raise ConfigurationError(
                "No builder registered for search provider",
                context={"provider": resolved.value, "supported": cls.supported_providers()},
            )
        instance = builder(settings, include_domains)
        logger.debug("search_provider_created", extra=instance.describe())
        return instance

    @staticmethod
    def _resolve(provider: str | SearchProvider) -> SearchProvider:
        if isinstance(provider, SearchProvider):
            return provider
        try:
            return SearchProvider(str(provider).strip().lower())
        except ValueError as error:
            raise ConfigurationError(
                "Unsupported search provider",
                context={"provider": str(provider), "supported": [p.value for p in SearchProvider]},
                cause=error,
            ) from error


def _build_tavily(settings: Settings, include_domains: Sequence[str] | None) -> SearchInterface:
    return TavilyProvider(
        api_key=settings.tvly_api_key,
        timeout_seconds=settings.request_timeout_seconds,
        max_retries=settings.max_retries,
        retry_backoff_seconds=settings.retry_backoff_seconds,
        include_domains=include_domains,
    )


def _build_mock(settings: Settings, include_domains: Sequence[str] | None) -> SearchInterface:
    return MockSearchProvider(domains=include_domains, results_per_query=min(settings.search_max_results, 5))


SearchFactory.register(SearchProvider.TAVILY, _build_tavily)
SearchFactory.register(SearchProvider.MOCK, _build_mock)
