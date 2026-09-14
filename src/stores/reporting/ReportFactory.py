"""Factory resolving the configured report renderer."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import ClassVar

from src.stores.reporting.providers.JinjaHTMLProvider import JinjaHTMLProvider, default_template_dir
from src.stores.reporting.providers.MockProvider import MockProvider as MockReportProvider
from src.stores.reporting.ReportEnums import ReportProvider
from src.stores.reporting.ReportInterface import ReportInterface
from src.utils.config import Settings
from src.utils.exceptions import ConfigurationError
from src.utils.logging import get_logger

__all__ = ["ReportFactory"]

logger = get_logger(__name__)


class ReportFactory:
    """Create the concrete report renderer selected by configuration."""

    _registry: ClassVar[dict[ReportProvider, Callable[[Settings, Path | str | None], ReportInterface]]] = {}

    @classmethod
    def register(
        cls,
        provider: ReportProvider,
        builder: Callable[[Settings, Path | str | None], ReportInterface],
    ) -> None:
        cls._registry[provider] = builder

    @classmethod
    def supported_providers(cls) -> list[str]:
        return sorted(provider.value for provider in cls._registry)

    @classmethod
    def create(
        cls,
        settings: Settings,
        provider: str | ReportProvider | None = None,
        *,
        template_dir: Path | str | None = None,
    ) -> ReportInterface:
        resolved = cls._resolve(provider if provider is not None else settings.report_provider)
        builder = cls._registry.get(resolved)
        if builder is None:  # pragma: no cover - defensive
            raise ConfigurationError(
                "No builder registered for report provider",
                context={"provider": resolved.value, "supported": cls.supported_providers()},
            )
        instance = builder(settings, template_dir)
        logger.debug("report_provider_created", extra=instance.describe())
        return instance

    @staticmethod
    def _resolve(provider: str | ReportProvider) -> ReportProvider:
        if isinstance(provider, ReportProvider):
            return provider
        try:
            return ReportProvider(str(provider).strip().lower())
        except ValueError as error:
            raise ConfigurationError(
                "Unsupported report provider",
                context={"provider": str(provider), "supported": [p.value for p in ReportProvider]},
                cause=error,
            ) from error


def _build_jinja(settings: Settings, template_dir: Path | str | None) -> ReportInterface:
    resolved_dir = Path(template_dir) if template_dir else _resolve_template_dir(settings)
    return JinjaHTMLProvider(resolved_dir, default_template=settings.report_template)


def _build_mock(settings: Settings, template_dir: Path | str | None) -> ReportInterface:
    return MockReportProvider()


def _resolve_template_dir(settings: Settings) -> Path:
    """Prefer the configured directory, fall back to the packaged ``templates/``."""
    configured = Path(settings.report_template_dir)
    if configured.is_dir():
        return configured
    fallback = default_template_dir()
    if fallback.is_dir():
        logger.debug(
            "report_template_dir_fallback",
            extra={"configured": str(configured), "resolved": str(fallback)},
        )
        return fallback
    return configured


ReportFactory.register(ReportProvider.JINJA, _build_jinja)
ReportFactory.register(ReportProvider.MOCK, _build_mock)
