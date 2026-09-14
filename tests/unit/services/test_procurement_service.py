"""Service orchestration tests.

The service is exercised with a fake workflow so that the deterministic
post-processing (filtering, deduplication, ranking, truncation, reporting,
persistence) is verified independently of any agent behaviour.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.persistence.models.procurement import (
    InMemoryProcurementRepository,
    ProcurementRecord,
    ProcurementRepository,
)
from src.schemas.procurement import (
    ProcurementRequest,
    ProcurementStatus,
    WorkflowResult,
)
from src.schemas.products import Product, ScrapedProduct
from src.schemas.reports import ProcurementReport, ReportFormat
from src.schemas.search import SearchResult
from src.services.procurement_service.procurement_service import ProcurementService
from src.services.procurement_service.workflow import ProcurementWorkflow
from src.stores.reporting.providers.JinjaHTMLProvider import JinjaHTMLProvider
from src.stores.reporting.providers.MockProvider import MockProvider as MockReportStore
from src.utils.config import Settings
from src.utils.exceptions import NotFoundError, PersistenceError, ReportGenerationError
from tests.conftest import TEMPLATES_DIR, make_settings


class FakeWorkflow(ProcurementWorkflow):
    """Returns a canned workflow result (or raises a canned error)."""

    name = "fake"

    def __init__(self, result: WorkflowResult | None = None, error: Exception | None = None) -> None:
        self._result = result or WorkflowResult()
        self._error = error

    def execute(self, request: ProcurementRequest) -> WorkflowResult:
        if self._error is not None:
            raise self._error
        return self._result


class FailingReportStore(MockReportStore):
    """Report renderer that always fails, to prove failures degrade gracefully."""

    def render(self, report, context, report_format=ReportFormat.HTML):  # type: ignore[no-untyped-def]
        raise ReportGenerationError("renderer exploded")


class FailingRepository(ProcurementRepository):
    def save(self, record: ProcurementRecord) -> None:
        raise PersistenceError("disk on fire")

    def get(self, request_id: str) -> ProcurementRecord | None:
        return None

    def list_recent(self, limit: int = 20) -> list[ProcurementRecord]:
        return []

    def close(self) -> None:
        return None


def _product(index: int, *, rank: int = 4, price: float = 100.0, url: str | None = None) -> Product:
    resolved = url or f"https://www.amazon.eg/product-{index}/dp/B00400OM{index}"
    return Product(
        page_url=resolved,
        product_url=resolved,
        product_title=f"Product {index}",
        product_current_price=price,
        product_currency="EGP",
        agent_recommendation_rank=rank,
        agent_recommendation_notes=[f"note {index}"],
    )


def _report() -> ProcurementReport:
    return ProcurementReport(
        title="Procurement Report: Coffee Machine",
        executive_summary="Summary",
        introduction="Intro",
        methodology="Method",
        findings="Findings",
        analysis="Analysis",
        recommendations=["Buy the cheap one"],
        conclusion="Conclusion",
        appendices="Appendix",
    )


def _service(
    workflow: ProcurementWorkflow,
    *,
    settings: Settings | None = None,
    report_store: Any = None,
    repository: ProcurementRepository | None = None,
) -> ProcurementService:
    return ProcurementService(
        workflow=workflow,
        report_store=report_store or MockReportStore(),
        repository=repository or InMemoryProcurementRepository(),
        settings=settings or make_settings(),
        provider_info={"llm": {"provider": "mock", "model": "mock-llm"}},
    )


class TestSuccessfulRun:
    def test_produces_complete_response(
        self,
        procurement_request: ProcurementRequest,
        mock_search: Any,
    ) -> None:
        results = mock_search.search("coffee machine", max_results=3)
        workflow = FakeWorkflow(
            WorkflowResult(
                queries=["coffee machine site:amazon.eg"],
                search_results=results,
                products=[_product(1, rank=5), _product(2, rank=3)],
                report=_report(),
            )
        )
        service = _service(workflow, report_store=JinjaHTMLProvider(TEMPLATES_DIR))

        response = service.run(procurement_request)

        assert response.status is ProcurementStatus.COMPLETED
        assert len(response.products) == 2
        assert response.products[0].agent_recommendation_rank == 5
        assert response.price_summary is not None
        assert response.price_summary.product_count == 2
        assert response.report is not None
        assert response.report.format is ReportFormat.HTML
        assert "<html" in response.report.content
        assert response.duration_ms >= 0
        assert response.metadata["workflow"] == "fake"
        assert response.metadata["providers"]["llm"]["provider"] == "mock"

    def test_response_is_persisted_and_retrievable(self, procurement_request: ProcurementRequest) -> None:
        repository = InMemoryProcurementRepository()
        workflow = FakeWorkflow(WorkflowResult(products=[_product(1)], report=_report()))
        service = _service(workflow, repository=repository)

        response = service.run(procurement_request)

        stored = service.get(response.request_id)
        assert stored.request_id == response.request_id
        assert repository.list_recent(limit=1)[0].product == procurement_request.product

    def test_report_markdown_format_is_honoured(self) -> None:
        request = ProcurementRequest(
            product="office chair",
            websites=["www.jumia.com.eg"],
            report_format=ReportFormat.MARKDOWN,
        )
        workflow = FakeWorkflow(WorkflowResult(products=[_product(1)], report=_report()))
        service = _service(workflow, report_store=JinjaHTMLProvider(TEMPLATES_DIR))

        response = service.run(request)

        assert response.report is not None
        assert response.report.format is ReportFormat.MARKDOWN
        assert response.report.content.startswith("# Procurement Report")


class TestDeterministicPostProcessing:
    def test_filters_invalid_products_and_duplicates(self, procurement_request: ProcurementRequest) -> None:
        valid = _product(1, rank=5)
        duplicate = _product(
            1, rank=4, price=90.0, url="https://www.amazon.eg/product-1/dp/B00400OM1?ref=abc"
        )
        zero_price = _product(2, price=0.0)
        invalid_url = _product(3, url="not-a-valid-url")

        workflow = FakeWorkflow(
            WorkflowResult(products=[valid, duplicate, zero_price, invalid_url], report=_report())
        )
        service = _service(workflow)

        response = service.run(procurement_request)

        assert len(response.products) == 1
        assert response.products[0].agent_recommendation_rank == 5
        assert any("removed" in warning for warning in response.warnings)

    def test_truncates_to_top_n(self) -> None:
        request = ProcurementRequest(product="office chair", websites=["www.jumia.com.eg"], top_n=2)
        workflow = FakeWorkflow(
            WorkflowResult(products=[_product(i, rank=5 - i) for i in range(5)], report=_report())
        )
        service = _service(workflow)

        response = service.run(request)

        assert len(response.products) == 2
        assert any("top 2" in warning for warning in response.warnings)

    def test_failed_status_when_nothing_is_found(self, procurement_request: ProcurementRequest) -> None:
        workflow = FakeWorkflow(WorkflowResult(products=[], report=None))
        service = _service(workflow)

        response = service.run(procurement_request)

        assert response.status is ProcurementStatus.FAILED
        assert response.products == []
        assert response.report is None

    def test_skips_report_when_not_requested(self) -> None:
        request = ProcurementRequest(
            product="office chair",
            websites=["www.jumia.com.eg"],
            generate_report=False,
        )
        workflow = FakeWorkflow(WorkflowResult(products=[_product(1)], report=_report()))
        service = _service(workflow)

        response = service.run(request)

        assert response.report is None
        assert response.status is ProcurementStatus.COMPLETED

    def test_search_results_are_revalidated_at_the_service_boundary(
        self, procurement_request: ProcurementRequest
    ) -> None:
        workflow = FakeWorkflow(
            WorkflowResult(
                search_results=[
                    SearchResult(url="https://www.amazon.eg/dp/B00400OMU0", score=0.95),
                    SearchResult(url="https://www.amazon.eg/login", score=0.99),
                    SearchResult(url="https://www.amazon.eg/dp/B082Y45678", score=0.01),
                ],
                products=[_product(1)],
                report=_report(),
            )
        )
        service = _service(workflow)

        response = service.run(procurement_request)

        assert len(response.search_results) == 1
        assert any("search result" in warning for warning in response.warnings)


class TestDegradation:
    def test_report_failure_marks_the_run_partial(
        self,
        procurement_request: ProcurementRequest,
    ) -> None:
        workflow = FakeWorkflow(WorkflowResult(products=[_product(1)], report=_report()))
        service = _service(workflow, report_store=FailingReportStore())

        response = service.run(procurement_request)

        assert response.status is ProcurementStatus.PARTIAL
        assert response.report is None
        assert any("rendering failed" in warning for warning in response.warnings)

    def test_missing_report_content_is_reported_as_a_warning(
        self, procurement_request: ProcurementRequest
    ) -> None:
        workflow = FakeWorkflow(WorkflowResult(products=[_product(1)], report=None))
        service = _service(workflow)

        response = service.run(procurement_request)

        assert response.status is ProcurementStatus.PARTIAL
        assert any("Report content was not produced" in warning for warning in response.warnings)

    def test_persistence_failure_does_not_lose_the_result(
        self, procurement_request: ProcurementRequest
    ) -> None:
        workflow = FakeWorkflow(WorkflowResult(products=[_product(1)], report=_report()))
        service = _service(workflow, repository=FailingRepository())

        response = service.run(procurement_request)

        assert response.status is ProcurementStatus.COMPLETED
        assert len(response.products) == 1
        assert any("could not be persisted" in warning for warning in response.warnings)

    def test_workflow_errors_propagate_unchanged(self, procurement_request: ProcurementRequest) -> None:
        error = ReportGenerationError("no report for you")
        service = _service(FakeWorkflow(error=error))

        with pytest.raises(ReportGenerationError):
            service.run(procurement_request)

    def test_get_unknown_request_id_raises_not_found(self) -> None:
        service = _service(FakeWorkflow())
        with pytest.raises(NotFoundError) as error:
            service.get("missing-request-id")
        assert error.value.http_status == 404


class TestSequentialWorkflowEndToEnd:
    """The credential-free workflow, exercised through the real service."""

    def test_runs_the_whole_pipeline_with_mock_providers(self, container: Any) -> None:
        request = ProcurementRequest(
            product="coffee machine for the office",
            websites=["www.amazon.eg", "www.jumia.com.eg"],
            max_queries=3,
            top_n=3,
        )

        response = container.procurement_controller.create_procurement(request)

        assert response.status is ProcurementStatus.COMPLETED
        assert 1 <= len(response.queries) <= 3
        assert response.search_results
        assert 1 <= len(response.products) <= 3
        assert response.price_summary is not None
        assert response.price_summary.product_count == len(response.products)
        assert response.report is not None
        assert "Price comparison" in response.report.content
        assert response.metadata["workflow"] == "sequential"

    def test_mock_pipeline_is_reproducible(self, container: Any) -> None:
        request = ProcurementRequest(
            product="coffee machine for the office",
            websites=["www.amazon.eg"],
            max_queries=2,
            top_n=2,
        )
        first = container.procurement_controller.create_procurement(request)
        second = container.procurement_controller.create_procurement(request)

        assert [p.product_current_price for p in first.products] == [
            p.product_current_price for p in second.products
        ]
        assert first.queries == second.queries


class TestScrapedProductHandling:
    def test_scraped_products_are_not_reported_without_ranking(
        self, procurement_request: ProcurementRequest, scraped_product: ScrapedProduct
    ) -> None:
        # A workflow that returns only scraped products (no ranking) must not
        # fabricate ranked products for the report.
        workflow = FakeWorkflow(WorkflowResult(scraped_products=[scraped_product], report=_report()))
        service = _service(workflow)

        response = service.run(procurement_request)

        assert response.products == []
