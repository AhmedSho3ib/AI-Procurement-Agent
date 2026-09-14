"""Report schema tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.schemas.products import PriceSummary
from src.schemas.reports import ProcurementReport, ReportContext, ReportFormat


class TestProcurementReport:
    def test_defaults_allow_partial_llm_output(self) -> None:
        report = ProcurementReport()
        assert report.title == "Procurement Report"
        assert report.recommendations == []

    def test_rejects_empty_title(self) -> None:
        with pytest.raises(ValidationError):
            ProcurementReport(title="")

    def test_parses_the_prototype_section_shape(self) -> None:
        report = ProcurementReport.model_validate(
            {
                "title": "Procurement Report: Coffee Machine",
                "executive_summary": "Summary",
                "introduction": "Intro",
                "methodology": "Method",
                "findings": "Findings",
                "analysis": "Analysis",
                "recommendations": ["Buy the De'Longhi"],
                "conclusion": "Conclusion",
                "appendices": "Appendix",
            }
        )
        assert report.recommendations == ["Buy the De'Longhi"]


class TestReportContext:
    def test_carries_only_data_no_logic(self) -> None:
        context = ReportContext(
            product_requirement="coffee machine",
            websites=["www.amazon.eg"],
            price_summary=PriceSummary(product_count=2, min_price=10.0, max_price=20.0),
        )
        assert context.company_name == "Rankyx"
        assert context.generated_at is not None
        assert context.price_summary is not None

    def test_report_formats_are_stable(self) -> None:
        assert [fmt.value for fmt in ReportFormat] == ["html", "markdown", "json"]
