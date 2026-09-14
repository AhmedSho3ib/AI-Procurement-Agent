"""Composition root.

This is the only place in the codebase that knows *both* the concrete providers
and the objects that consume them. Everything is assembled here and injected
downwards, which is what makes the rest of the application testable: a test can
build the same container with mock providers and no credentials.

Dependency direction stays one-way:

    Container -> controllers -> services -> (stores, persistence, rules)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.controllers.procurement_ctrl import ProcurementController
from src.persistence.models.procurement import ProcurementRepository, build_repository
from src.services.procurement_service.procurement_service import ProcurementService, build_workflow
from src.services.procurement_service.workflow import ProcurementWorkflow
from src.stores.llm import LLMFactory, LLMInterface
from src.stores.reporting import ReportFactory, ReportInterface
from src.stores.scraping import ScraperFactory, ScraperInterface
from src.stores.search import SearchFactory, SearchInterface
from src.utils.config import Settings, get_settings
from src.utils.logging import configure_logging, get_logger
from src.utils.observability import ObservabilityManager

__all__ = ["AppContainer", "build_container"]

logger = get_logger(__name__)


@dataclass(slots=True)
class AppContainer:
    """Fully wired object graph for one application instance."""

    settings: Settings
    observability: ObservabilityManager
    llm: LLMInterface
    search_store: SearchInterface
    scraper_store: ScraperInterface
    report_store: ReportInterface
    repository: ProcurementRepository
    workflow: ProcurementWorkflow
    procurement_service: ProcurementService
    procurement_controller: ProcurementController
    provider_info: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------- lifecycle
    def start(self) -> None:
        """Start long-lived integrations (observability)."""
        self.observability.start()

    def shutdown(self) -> None:
        """Release resources in reverse order of acquisition."""
        self.observability.end()
        try:
            self.repository.close()
        except Exception as error:  # noqa: BLE001 - shutdown must never raise
            logger.warning("repository_close_failed", extra={"error": type(error).__name__})


def build_container(settings: Settings | None = None) -> AppContainer:
    """Build the application object graph from configuration."""
    resolved = settings or get_settings()
    configure_logging(resolved.log_level, resolved.log_format)

    observability = ObservabilityManager(resolved)
    llm = LLMFactory.create(resolved)
    search_store = SearchFactory.create(resolved)
    scraper_store = ScraperFactory.create(resolved)
    report_store = ReportFactory.create(resolved)
    repository = build_repository(resolved)

    workflow = build_workflow(
        llm=llm,
        search_store=search_store,
        scraper_store=scraper_store,
        settings=resolved,
    )

    provider_info: dict[str, Any] = {
        "llm": {"provider": llm.provider.value, "model": llm.model_name},
        "search": search_store.describe(),
        "scraper": scraper_store.describe(),
        "report": report_store.describe(),
        "persistence": {"backend": resolved.persistence_backend},
    }

    service = ProcurementService(
        workflow=workflow,
        report_store=report_store,
        repository=repository,
        settings=resolved,
        observability=observability,
        provider_info=provider_info,
    )
    controller = ProcurementController(service, resolved)

    logger.debug(
        "container_built",
        extra={"workflow": workflow.name, "environment": resolved.environment},
    )
    return AppContainer(
        settings=resolved,
        observability=observability,
        llm=llm,
        search_store=search_store,
        scraper_store=scraper_store,
        report_store=report_store,
        repository=repository,
        workflow=workflow,
        procurement_service=service,
        procurement_controller=controller,
        provider_info=provider_info,
    )
