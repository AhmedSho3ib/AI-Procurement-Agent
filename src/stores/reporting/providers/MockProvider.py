"""Deterministic, dependency-light report renderer used for tests and CI."""

from __future__ import annotations

from src.schemas.reports import ProcurementReport, RenderedReport, ReportContext, ReportFormat
from src.stores.reporting.ReportEnums import ReportProvider
from src.stores.reporting.ReportInterface import ReportInterface

__all__ = ["MockProvider"]


class MockProvider(ReportInterface):
    """Renders a compact, deterministic representation of the report content."""

    provider = ReportProvider.MOCK

    def render(
        self,
        report: ProcurementReport,
        context: ReportContext,
        report_format: ReportFormat = ReportFormat.HTML,
    ) -> RenderedReport:
        lines = [
            f"# {report.title}",
            "",
            f"Company: {context.company_name}",
            f"Requirement: {context.product_requirement}",
            f"Country: {context.country} | Language: {context.language}",
            f"Products compared: {len(context.products)}",
            "",
            "## Executive Summary",
            report.executive_summary,
            "",
            "## Introduction",
            report.introduction,
            "",
            "## Methodology",
            report.methodology,
            "",
            "## Findings",
            report.findings,
            "",
            "## Analysis",
            report.analysis,
            "",
            "## Recommendations",
            *[f"- {item}" for item in report.recommendations],
            "",
            "## Conclusion",
            report.conclusion,
            "",
            "## Appendices",
            report.appendices,
            "",
        ]
        content = "\n".join(lines)
        if report_format is ReportFormat.JSON:
            import json

            content = json.dumps(
                {"report": report.model_dump(mode="json"), "context": context.model_dump(mode="json")},
                indent=2,
                ensure_ascii=False,
            )

        return RenderedReport(format=report_format, content=content, generated_at=context.generated_at)
