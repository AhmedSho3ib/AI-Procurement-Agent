"""Schema contract tests: validation rules only, no business logic."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.schemas.procurement import ProcurementRequest, ProcurementStatus, WorkflowResult
from src.schemas.reports import ReportFormat


class TestProcurementRequest:
    def test_accepts_the_documented_api_payload(self) -> None:
        request = ProcurementRequest(
            product="coffee machine for the office",
            websites=["Amazon Egypt", "Jumia Egypt", "Noon Egypt"],
            country="Egypt",
            language="English",
            max_queries=10,
            score_threshold=0.10,
            top_n=10,
        )
        assert request.product == "coffee machine for the office"
        assert request.max_queries == 10
        assert request.report_format is ReportFormat.HTML
        assert request.generate_report is True

    def test_normalises_and_deduplicates_websites(self) -> None:
        request = ProcurementRequest(
            product="office chair",
            websites=["  WWW.Amazon.EG ", "www.amazon.eg", "www.jumia.com.eg"],
        )
        assert request.websites == ["www.amazon.eg", "www.jumia.com.eg"]

    def test_rejects_blank_product(self) -> None:
        with pytest.raises(ValidationError):
            ProcurementRequest(product="   ", websites=["www.amazon.eg"])

    def test_rejects_empty_website_list(self) -> None:
        with pytest.raises(ValidationError):
            ProcurementRequest(product="office chair", websites=[])

    def test_rejects_out_of_range_score_threshold(self) -> None:
        with pytest.raises(ValidationError):
            ProcurementRequest(product="office chair", websites=["x.com"], score_threshold=1.5)

    def test_rejects_excessive_max_queries(self) -> None:
        with pytest.raises(ValidationError):
            ProcurementRequest(product="office chair", websites=["x.com"], max_queries=100)

    def test_strips_whitespace_from_text_fields(self) -> None:
        request = ProcurementRequest(
            product="  standing desk  ",
            websites=["www.amazon.eg"],
            country="  Egypt  ",
            company_name="  Rankyx  ",
        )
        assert (request.product, request.country, request.company_name) == (
            "standing desk",
            "Egypt",
            "Rankyx",
        )

    def test_rejects_unknown_report_format(self) -> None:
        with pytest.raises(ValidationError):
            ProcurementRequest(product="office chair", websites=["x.com"], report_format="pdf")


class TestWorkflowResult:
    def test_defaults_to_empty_collections(self) -> None:
        result = WorkflowResult()
        assert result.queries == []
        assert result.products == []
        assert result.report is None
        assert result.warnings == []


class TestProcurementStatus:
    def test_status_values_are_stable(self) -> None:
        assert [status.value for status in ProcurementStatus] == ["completed", "partial", "failed"]
