"""Report data contracts.

The LLM produces :class:`ProcurementReport` (content only). The reporting store
renders that content against :class:`ReportContext` into a
:class:`RenderedReport`. Presentation never lives inside the agents.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from src.schemas.products import PriceSummary, Product

__all__ = ["ProcurementReport", "RenderedReport", "ReportContext", "ReportFormat"]


class ReportFormat(StrEnum):
    """Supported report renderings."""

    HTML = "html"
    MARKDOWN = "markdown"
    JSON = "json"


class ProcurementReport(BaseModel):
    """The eight procurement report sections requested by the prototype.

    Section order mirrors the original brief: Executive Summary, Introduction,
    Methodology, Findings, Analysis, Recommendations, Conclusion, Appendices.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(default="Procurement Report", min_length=1, max_length=200)
    executive_summary: str = Field(default="", max_length=6000)
    introduction: str = Field(default="", max_length=6000)
    methodology: str = Field(default="", max_length=6000)
    findings: str = Field(default="", max_length=12000)
    analysis: str = Field(default="", max_length=12000)
    recommendations: list[str] = Field(default_factory=list, max_length=20)
    conclusion: str = Field(default="", max_length=6000)
    appendices: str = Field(default="", max_length=6000)


class ReportContext(BaseModel):
    """Deterministic context supplied to a renderer.

    Everything a template needs that is *not* LLM prose. The service fills this
    in so renderers stay free of business logic.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    product_requirement: str
    company_name: str = "Rankyx"
    websites: list[str] = Field(default_factory=list)
    country: str = "Egypt"
    language: str = "English"
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    queries: list[str] = Field(default_factory=list)
    products: list[Product] = Field(default_factory=list)
    price_summary: PriceSummary | None = None
    search_results_considered: int = Field(default=0, ge=0)
    warnings: list[str] = Field(default_factory=list)


class RenderedReport(BaseModel):
    """A rendered report artifact."""

    format: ReportFormat
    content: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
