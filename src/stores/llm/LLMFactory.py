"""Factory resolving the configured LLM provider.

This is the only place that knows how to translate configuration into a concrete
:class:`LLMInterface`. Callers (services) depend on the interface, so swapping
Gemini for another vendor is a registry entry, not a refactor.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import ClassVar

from src.stores.llm.LLMEnums import LLMProvider
from src.stores.llm.LLMInterface import LLMInterface
from src.stores.llm.providers.GeminiProvider import GeminiProvider
from src.stores.llm.providers.MockProvider import MockProvider
from src.utils.config import Settings
from src.utils.exceptions import ConfigurationError
from src.utils.logging import get_logger

__all__ = ["LLMFactory"]

logger = get_logger(__name__)


class LLMFactory:
    """Create the concrete LLM provider selected by configuration."""

    _registry: ClassVar[dict[LLMProvider, Callable[[Settings], LLMInterface]]] = {}

    @classmethod
    def register(
        cls,
        provider: LLMProvider,
        builder: Callable[[Settings], LLMInterface],
    ) -> None:
        """Register (or override) a provider builder."""
        cls._registry[provider] = builder

    @classmethod
    def supported_providers(cls) -> list[str]:
        return sorted(provider.value for provider in cls._registry)

    @classmethod
    def create(
        cls,
        settings: Settings,
        provider: str | LLMProvider | None = None,
    ) -> LLMInterface:
        """Instantiate the provider named by ``provider`` or ``settings.llm_provider``."""
        resolved = cls._resolve(provider if provider is not None else settings.llm_provider)
        builder = cls._registry.get(resolved)
        if builder is None:  # pragma: no cover - defensive, covered by _resolve
            raise ConfigurationError(
                "No builder registered for LLM provider",
                context={"provider": resolved.value, "supported": cls.supported_providers()},
            )
        instance = builder(settings)
        logger.debug("llm_provider_created", extra={"provider": resolved.value, "model": instance.model_name})
        return instance

    @staticmethod
    def _resolve(provider: str | LLMProvider) -> LLMProvider:
        if isinstance(provider, LLMProvider):
            return provider
        try:
            return LLMProvider(str(provider).strip().lower())
        except ValueError as error:
            raise ConfigurationError(
                "Unsupported LLM provider",
                context={"provider": str(provider), "supported": [p.value for p in LLMProvider]},
                cause=error,
            ) from error


def _build_gemini(settings: Settings) -> LLMInterface:
    return GeminiProvider(
        api_key=settings.google_api_key,
        model=settings.google_model,
        temperature=settings.llm_temperature,
        max_retries=settings.max_retries,
        retry_backoff_seconds=settings.retry_backoff_seconds,
    )


def _build_mock(settings: Settings) -> LLMInterface:
    return MockProvider(model="mock-llm", temperature=settings.llm_temperature)


LLMFactory.register(LLMProvider.GEMINI, _build_gemini)
LLMFactory.register(LLMProvider.MOCK, _build_mock)
