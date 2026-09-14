"""Top level procurement request/response contracts.

The public API surface is defined entirely by these models, which is why they
carry no business logic: validation only, and only at the boundary.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.schemas.products import PriceSummary, Product, ScrapedProduct
from src.schemas.reports import ProcurementReport, RenderedReport, ReportFormat
from src.schemas.search import SearchResult

__all__ = [
    "ProcurementRequest",
    "ProcurementResponse",
    "ProcurementStatus",
    "WorkflowResult",
]


class ProcurementStatus(StrEnum):
    """Outcome of a procurement run."""

    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class ProcurementRequest(BaseModel):
    """Input for a procurement research run.

    Field names follow the original notebook inputs while staying close to the
    API example in the project brief:

    ``product``            -> ``product_name``
    ``websites``           -> ``websites_list``
    ``country``            -> ``country_name``
    ``max_queries``        -> ``no_keywords``
    ``score_threshold``    -> ``score_th``
    ``top_n``              -> ``top_recommendations_no``
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    product: str = Field(min_length=3, max_length=300, description="What the company wants to buy")
    websites: list[str] = Field(
        min_length=1,
        max_length=20,
        description="Target websites/domains to source products from",
    )
    country: str = Field(default="Egypt", min_length=2, max_length=80)
    language: str = Field(default="English", min_length=2, max_length=40)
    max_queries: int = Field(default=10, ge=1, le=20, description="Maximum number of search queries")
    score_threshold: float = Field(
        default=0.10, ge=0.0, le=1.0, description="Minimum provider relevance score to keep a result"
    )
    top_n: int = Field(default=10, ge=1, le=50, description="Number of products to keep in the report")
    company_name: str = Field(default="Rankyx", min_length=1, max_length=120)
    report_format: ReportFormat = Field(default=ReportFormat.HTML)
    generate_report: bool = Field(default=True, description="Set to false to skip report rendering")

    @field_validator("websites")
    @classmethod
    def _normalise_websites(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip().lower() for item in value if item and item.strip()]
        if not cleaned:
            raise ValueError("websites must contain at least one non-empty entry")
        # Preserve order while removing duplicates.
        return list(dict.fromkeys(cleaned))

    @field_validator("product", "country", "language", "company_name")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()


class WorkflowResult(BaseModel):
    """Internal contract returned by a procurement workflow.

    A workflow (CrewAI agents or the deterministic sequential executor) returns
    *raw-ish* structured output; the service then applies deterministic
    validation, filtering, deduplication and ranking rules before responding.
    This is what keeps "LLM for reasoning, code for rules" enforceable.
    """

    queries: list[str] = Field(default_factory=list)
    search_results: list[SearchResult] = Field(default_factory=list)
    products: list[Product] = Field(default_factory=list)
    scraped_products: list[ScrapedProduct] = Field(default_factory=list)
    report: ProcurementReport | None = None
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProcurementResponse(BaseModel):
    """Response returned by ``POST /api/v1/procurement``."""

    request_id: str
    status: ProcurementStatus
    request: ProcurementRequest
    queries: list[str] = Field(default_factory=list)
    search_results: list[SearchResult] = Field(default_factory=list)
    products: list[Product] = Field(default_factory=list)
    price_summary: PriceSummary | None = None
    report: RenderedReport | None = None
    warnings: list[str] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    duration_ms: int = Field(default=0, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)
