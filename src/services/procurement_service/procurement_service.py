"""The procurement service: core business workflow orchestration.

Responsibilities
----------------
* Run the configured workflow (agentic or deterministic).
* Apply the deterministic business rules to whatever the workflow produced.
* Build the report context and render the report through the reporting store.
* Persist the run through the persistence layer.
* Return a fully populated :class:`ProcurementResponse`.

Non-responsibilities
--------------------
* No HTTP/transport awareness.
* No provider/SDK knowledge (only interfaces).
* No prompt or agent construction (that lives in ``crew.py`` / ``sequential.py``).
"""

from __future__ import annotations

import time
from typing import Any
from uuid import uuid4

from src.persistence.models.procurement import ProcurementRecord, ProcurementRepository
from src.schemas.procurement import ProcurementRequest, ProcurementResponse, ProcurementStatus
from src.schemas.products import PriceSummary, Product
from src.schemas.reports import ProcurementReport, RenderedReport, ReportContext
from src.schemas.search import SearchResult
from src.services.procurement_service.crew import CrewAIProcurementWorkflow
from src.services.procurement_service.prompts import PromptLibrary
from src.services.procurement_service.rules import (
    build_price_summary,
    deduplicate_products,
    deduplicate_search_results,
    filter_products,
    filter_search_results,
    select_top_products,
)
from src.services.procurement_service.sequential import SequentialProcurementWorkflow
from src.services.procurement_service.workflow import ProcurementWorkflow
from src.stores.llm import LLMInterface
from src.stores.reporting import ReportInterface
from src.stores.scraping import ScraperInterface
from src.stores.search import SearchInterface
from src.utils.config import Settings
from src.utils.exceptions import ConfigurationError, NotFoundError, PersistenceError, ReportGenerationError
from src.utils.logging import get_logger
from src.utils.observability import ObservabilityManager

__all__ = ["ProcurementService", "build_workflow"]

logger = get_logger(__name__)


class ProcurementService:
    """Coordinates a procurement run from request to persisted response."""

    def __init__(
        self,
        *,
        workflow: ProcurementWorkflow,
        report_store: ReportInterface,
        repository: ProcurementRepository,
        settings: Settings,
        observability: ObservabilityManager | None = None,
        provider_info: dict[str, Any] | None = None,
    ) -> None:
        self._workflow = workflow
        self._report_store = report_store
        self._repository = repository
        self._settings = settings
        self._observability = observability
        self._provider_info = dict(provider_info or {})

    # ------------------------------------------------------------------ public
    def run(self, request: ProcurementRequest) -> ProcurementResponse:
        """Execute a procurement run and return the response contract."""
        request_id = str(uuid4())
        started = time.perf_counter()
        logger.info(
            "procurement_started",
            extra={
                "request_id": request_id,
                "product": request.product,
                "websites": request.websites,
                "workflow": self._workflow.name,
            },
        )

        result = self._workflow.execute(request)
        warnings: list[str] = list(result.warnings)

        search_results = self._post_process_search_results(request, result.search_results, warnings)
        products = self._post_process_products(request, result.products, warnings)
        price_summary = build_price_summary(products)

        report = self._render_report(
            request,
            result.report,
            result.queries,
            products,
            price_summary,
            search_results,
            warnings,
        )

        duration_ms = int((time.perf_counter() - started) * 1000)
        status = _determine_status(
            products=products,
            report=report,
            report_requested=request.generate_report,
        )

        response = ProcurementResponse(
            request_id=request_id,
            status=status,
            request=request,
            queries=result.queries,
            search_results=search_results,
            products=products,
            price_summary=price_summary,
            report=report,
            warnings=warnings,
            duration_ms=duration_ms,
            metadata=self._build_metadata(
                result_metadata=result.metadata,
                search_kept=len(search_results),
                products_kept=len(products),
            ),
        )

        persistence_warning = self._persist(response)
        if persistence_warning:
            response.warnings.append(persistence_warning)

        logger.info(
            "procurement_completed",
            extra={
                "request_id": request_id,
                "status": status.value,
                "products": len(products),
                "duration_ms": duration_ms,
                "report": report is not None,
            },
        )
        return response

    def get(self, request_id: str) -> ProcurementResponse:
        """Return a previously persisted procurement run."""
        record = self._repository.get(request_id)
        if record is None:
            raise NotFoundError(
                "No procurement run found for the given id",
                context={"request_id": request_id},
            )
        return record.response

    # ------------------------------------------------------------------ steps
    def _post_process_search_results(
        self,
        request: ProcurementRequest,
        results: list[SearchResult],
        warnings: list[str],
    ) -> list[SearchResult]:
        """Guarantee the search contract regardless of what the workflow returned."""
        if not results:
            return []
        outcome = filter_search_results(
            deduplicate_search_results(results),
            score_threshold=request.score_threshold,
        )
        if outcome.rejected_count:
            warnings.append(
                f"Service-level validation removed {outcome.rejected_count} search result(s) "
                "that did not satisfy the URL/score rules."
            )
        return outcome.kept

    def _post_process_products(
        self,
        request: ProcurementRequest,
        products: list[Product],
        warnings: list[str],
    ) -> list[Product]:
        """Filter, deduplicate, rank and truncate the product set."""
        if not products:
            return []
        outcome = filter_products(products)
        if outcome.rejected_count:
            warnings.append(f"Service-level validation removed {outcome.rejected_count} product(s).")
        kept = [product for product in outcome.kept if isinstance(product, Product)]
        deduplicated = deduplicate_products(kept)
        selected, dropped = select_top_products(deduplicated, request.top_n)
        if dropped:
            warnings.append(
                f"{len(dropped)} product(s) were outside the top {request.top_n} "
                "and excluded from the report."
            )
        return selected

    def _render_report(
        self,
        request: ProcurementRequest,
        report_content: ProcurementReport | None,
        queries: list[str],
        products: list[Product],
        price_summary: PriceSummary,
        search_results: list[SearchResult],
        warnings: list[str],
    ) -> RenderedReport | None:
        if not request.generate_report:
            return None
        if report_content is None:
            warnings.append("Report content was not produced by the workflow, so no report was rendered.")
            return None

        context = ReportContext(
            product_requirement=request.product,
            company_name=request.company_name,
            websites=request.websites,
            country=request.country,
            language=request.language,
            queries=queries,
            products=products,
            price_summary=price_summary,
            search_results_considered=len(search_results),
            warnings=warnings,
        )
        try:
            return self._report_store.render(report_content, context, request.report_format)
        except ReportGenerationError as error:
            warnings.append(f"Report rendering failed: {error.message}")
            logger.error("report_rendering_failed", extra={"error": error.code})
            return None

    def _persist(self, response: ProcurementResponse) -> str | None:
        """Persist the run. A storage outage must not destroy a completed result.

        Returns a warning message when persistence failed so the caller can attach
        it to the (already built) response.
        """
        try:
            self._repository.save(ProcurementRecord.from_response(response))
        except PersistenceError as error:
            logger.error(
                "procurement_persistence_failed",
                extra={"request_id": response.request_id, "error": error.code},
            )
            return "The procurement run could not be persisted for later retrieval."
        return None

    def _build_metadata(
        self,
        *,
        result_metadata: dict[str, Any],
        search_kept: int,
        products_kept: int,
    ) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "workflow": self._workflow.name,
            "providers": self._provider_info,
            "counts": {"search_results_kept": search_kept, "products_kept": products_kept},
            "workflow_metadata": result_metadata,
        }
        if self._observability is not None:
            metadata["observability"] = self._observability.describe()
        return metadata


def _determine_status(
    *,
    products: list[Product],
    report: RenderedReport | None,
    report_requested: bool,
) -> ProcurementStatus:
    if products and (report is not None or not report_requested):
        return ProcurementStatus.COMPLETED
    if products or report is not None:
        return ProcurementStatus.PARTIAL
    return ProcurementStatus.FAILED


def build_workflow(
    *,
    llm: LLMInterface,
    search_store: SearchInterface,
    scraper_store: ScraperInterface,
    settings: Settings,
    prompts: PromptLibrary | None = None,
) -> ProcurementWorkflow:
    """Create the workflow selected by ``WORKFLOW`` (``crew`` or ``sequential``).

    Both implementations accept the same collaborators, so switching strategies
    is a configuration change and never a code change.
    """
    shared: dict[str, Any] = {
        "llm": llm,
        "search_store": search_store,
        "scraper_store": scraper_store,
        "settings": settings,
        "prompts": prompts,
    }
    if settings.workflow == "crew":
        return CrewAIProcurementWorkflow(**shared)
    if settings.workflow == "sequential":
        return SequentialProcurementWorkflow(**shared)
    raise ConfigurationError(
        "Unsupported WORKFLOW value",
        context={"workflow": settings.workflow, "supported": ["crew", "sequential"]},
    )
