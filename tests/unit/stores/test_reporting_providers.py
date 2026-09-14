"""Report rendering: Jinja templates, all three formats, and failure modes."""

from __future__ import annotations

import json

import pytest

from src.schemas.products import PriceSummary, Product
from src.schemas.reports import ProcurementReport, ReportContext, ReportFormat
from src.stores.reporting.providers.JinjaHTMLProvider import JinjaHTMLProvider
from src.stores.reporting.providers.MockProvider import MockProvider as MockReportProvider
from src.utils.exceptions import ConfigurationError, ReportGenerationError
from tests.conftest import TEMPLATES_DIR


@pytest.fixture
def report() -> ProcurementReport:
    return ProcurementReport(
        title="Procurement Report: Coffee Machine",
        executive_summary="Prices range widely across the three marketplaces.",
        introduction="This report supports the office coffee machine purchase.",
        methodology="Queries were generated, executed and validated before extraction.",
        findings="Ten products were compared.",
        analysis="The market segments cleanly into drip and automatic machines.",
        recommendations=["Buy the De'Longhi Magnifica S", "Re-validate prices before ordering"],
        conclusion="Proceed with the shortlisted automatic machine.",
        appendices="Raw data is attached in the machine readable response.",
    )


@pytest.fixture
def context(product: Product) -> ReportContext:
    return ReportContext(
        product_requirement="coffee machine for the office",
        company_name="Rankyx",
        websites=["www.amazon.eg", "www.jumia.com.eg"],
        country="Egypt",
        queries=["coffee machine site:amazon.eg"],
        products=[product],
        price_summary=PriceSummary(
            product_count=1,
            currency="EGP",
            min_price=24999.0,
            max_price=24999.0,
            average_price=24999.0,
            median_price=24999.0,
            cheapest_product_title=product.product_title,
        ),
        search_results_considered=10,
        warnings=["One search query failed."],
    )


class TestJinjaHTMLProvider:
    def test_renders_a_professional_html_report(
        self, jinja_report: JinjaHTMLProvider, report: ProcurementReport, context: ReportContext
    ) -> None:
        rendered = jinja_report.render(report, context)

        assert rendered.format is ReportFormat.HTML
        content = rendered.content
        assert content.startswith("<!DOCTYPE html>")
        assert "bootstrap" in content.lower()
        assert "Procurement Report: Coffee Machine" in content
        assert "Rankyx" in content
        for heading in (
            "Executive Summary",
            "Introduction",
            "Methodology",
            "Findings",
            "Analysis",
            "Recommendations",
            "Conclusion",
            "Appendices",
        ):
            assert heading in content

    def test_includes_products_prices_and_notes(
        self, jinja_report: JinjaHTMLProvider, report: ProcurementReport, context: ReportContext
    ) -> None:
        content = jinja_report.render(report, context).content
        # Titles are HTML-escaped, so the apostrophe in "De'Longhi" is encoded.
        assert "Magnifica S Automatic Coffee Machine" in content
        assert "De&#39;Longhi" in content
        assert "24,999.00" in content  # money filter
        assert "Best value for an office environment." in content
        assert "EGP" in content

    def test_includes_warnings_and_queries(
        self, jinja_report: JinjaHTMLProvider, report: ProcurementReport, context: ReportContext
    ) -> None:
        content = jinja_report.render(report, context).content
        assert "One search query failed." in content
        assert "coffee machine site:amazon.eg" in content

    def test_escapes_html_from_untrusted_sources(
        self, jinja_report: JinjaHTMLProvider, report: ProcurementReport, context: ReportContext
    ) -> None:
        # Product titles come from scraped pages: they must never inject markup.
        hostile = Product(
            page_url="https://www.amazon.eg/dp/B00400OMU0",
            product_title="<script>alert('xss')</script>",
            product_current_price=10.0,
        )
        hostile_context = context.model_copy(update={"products": [hostile]})

        content = jinja_report.render(report, hostile_context).content

        assert "<script>alert('xss')</script>" not in content
        assert "&lt;script&gt;" in content

    def test_renders_markdown(
        self, jinja_report: JinjaHTMLProvider, report: ProcurementReport, context: ReportContext
    ) -> None:
        rendered = jinja_report.render(report, context, ReportFormat.MARKDOWN)
        assert rendered.format is ReportFormat.MARKDOWN
        assert rendered.content.startswith("# Procurement Report: Coffee Machine")
        assert "| Rank | Product |" in rendered.content

    def test_renders_json(
        self, jinja_report: JinjaHTMLProvider, report: ProcurementReport, context: ReportContext
    ) -> None:
        rendered = jinja_report.render(report, context, ReportFormat.JSON)
        payload = json.loads(rendered.content)
        assert payload["report"]["title"] == report.title
        assert payload["context"]["company_name"] == "Rankyx"

    def test_handles_a_report_without_products(
        self, jinja_report: JinjaHTMLProvider, report: ProcurementReport
    ) -> None:
        empty_context = ReportContext(product_requirement="coffee machine")
        content = jinja_report.render(report, empty_context).content
        assert "No product passed deterministic validation" in content

    def test_missing_template_directory_is_a_configuration_error(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        with pytest.raises(ConfigurationError):
            JinjaHTMLProvider(tmp_path / "missing")

    def test_missing_template_file_is_a_configuration_error(
        self, report: ProcurementReport, context: ReportContext
    ) -> None:
        provider = JinjaHTMLProvider(TEMPLATES_DIR, default_template="not_a_template.html")
        with pytest.raises(ConfigurationError):
            provider.render(report, context)

    def test_broken_template_becomes_a_report_generation_error(
        self,
        tmp_path,
        report: ProcurementReport,
        context: ReportContext,  # type: ignore[no-untyped-def]
    ) -> None:
        (tmp_path / "broken.html").write_text("{{ report.title | nonexistent_filter }}", encoding="utf-8")
        provider = JinjaHTMLProvider(tmp_path, default_template="broken.html")
        with pytest.raises(ReportGenerationError):
            provider.render(report, context)

    def test_describe_lists_available_templates(self, jinja_report: JinjaHTMLProvider) -> None:
        described = jinja_report.describe()
        assert "procurement_report.html" in described["templates"]


class TestMockReportProvider:
    def test_renders_markdown_like_content(
        self, mock_report: MockReportProvider, report: ProcurementReport, context: ReportContext
    ) -> None:
        rendered = mock_report.render(report, context)
        assert rendered.format is ReportFormat.HTML
        assert "# Procurement Report: Coffee Machine" in rendered.content

    def test_supports_json(
        self, mock_report: MockReportProvider, report: ProcurementReport, context: ReportContext
    ) -> None:
        rendered = mock_report.render(report, context, ReportFormat.JSON)
        assert json.loads(rendered.content)["report"]["title"] == report.title
