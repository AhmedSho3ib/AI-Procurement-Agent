"""Deterministic filtering and ranking rules.

Applied by the service *after* the LLM workflow, so the guarantees (no invalid
URLs, no zero prices, no duplicate listings, ranked output) hold regardless of
what a model produced.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Generic, TypeVar

from src.schemas.products import PriceSummary, Product, ScrapedProduct
from src.schemas.search import SearchResult
from src.services.procurement_service.rules.url_validation import validate_url
from src.utils.logging import get_logger

__all__ = [
    "FilterOutcome",
    "build_price_summary",
    "filter_products",
    "filter_search_results",
    "rank_products",
    "select_top_products",
]

logger = get_logger(__name__)

T = TypeVar("T")

#: A price below this is treated as "not extracted" rather than a real price.
MIN_ACCEPTABLE_PRICE = 0.0


@dataclass(slots=True)
class FilterOutcome(Generic[T]):
    """Result of a deterministic filtering pass."""

    kept: list[T] = field(default_factory=list)
    rejected: list[T] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    @property
    def rejected_count(self) -> int:
        return len(self.rejected)

    def add_rejection(self, item: T, reason: str) -> None:
        self.rejected.append(item)
        self.reasons.append(reason)


def filter_search_results(
    results: Sequence[SearchResult],
    *,
    score_threshold: float,
    allowed_domains: frozenset[str] | set[str] | None = None,
) -> FilterOutcome[SearchResult]:
    """Keep only credible, in-scope, single-product search results.

    Rejections are recorded with a human readable reason so that the API can
    explain *why* a candidate was dropped instead of silently losing data.
    """
    outcome: FilterOutcome[SearchResult] = FilterOutcome()
    for result in results:
        if result.score < score_threshold:
            outcome.add_rejection(
                result,
                f"below score threshold {score_threshold:.2f} (score={result.score:.2f})",
            )
            continue

        verdict = validate_url(result.url, allowed_domains=allowed_domains)
        if not verdict.is_usable:
            outcome.add_rejection(result, f"{result.url} -> {verdict.describe()}")
            continue

        outcome.kept.append(result)
    return outcome


def filter_products(
    products: Iterable[Product | ScrapedProduct],
    *,
    min_price: float = MIN_ACCEPTABLE_PRICE,
) -> FilterOutcome[Product | ScrapedProduct]:
    """Drop products that cannot be compared: no URL, no title, no valid price."""
    outcome: FilterOutcome[Product | ScrapedProduct] = FilterOutcome()
    for product in products:
        url = product.product_url or product.page_url
        if not url or not url.strip():
            outcome.add_rejection(product, "missing product URL")
            continue

        verdict = validate_url(url, require_product_page=False)
        if not verdict.is_valid:
            outcome.add_rejection(product, f"invalid product URL: {verdict.describe()}")
            continue

        title = (product.product_title or "").strip()
        if not title:
            outcome.add_rejection(product, f"empty product title for {url}")
            continue

        if product.product_current_price <= min_price:
            outcome.add_rejection(product, f"missing or non-positive price for {url}")
            continue

        outcome.kept.append(product)
    return outcome


def rank_products(products: Sequence[Product]) -> list[Product]:
    """Order products best-first: recommendation rank desc, then price asc."""

    def sort_key(product: Product) -> tuple[int, float, str]:
        return (-product.agent_recommendation_rank, product.product_current_price, product.effective_url)

    return sorted(products, key=sort_key)


def select_top_products(products: Sequence[Product], top_n: int) -> tuple[list[Product], list[Product]]:
    """Return ``(selected, dropped)`` after ranking."""
    ranked = rank_products(products)
    limit = max(1, top_n)
    return ranked[:limit], ranked[limit:]


def build_price_summary(products: Sequence[Product]) -> PriceSummary:
    """Deterministic price statistics used by the report's findings section."""
    priced = [product for product in products if product.product_current_price > 0]
    if not priced:
        return PriceSummary(product_count=0)

    prices = [product.product_current_price for product in priced]
    cheapest = min(priced, key=lambda product: product.product_current_price)
    most_expensive = max(priced, key=lambda product: product.product_current_price)
    currency = next((product.product_currency for product in priced if product.product_currency), None)

    return PriceSummary(
        product_count=len(priced),
        currency=currency,
        min_price=round(min(prices), 2),
        max_price=round(max(prices), 2),
        average_price=round(statistics.fmean(prices), 2),
        median_price=round(statistics.median(prices), 2),
        cheapest_product_title=cheapest.product_title,
        cheapest_product_url=cheapest.effective_url,
        most_expensive_product_title=most_expensive.product_title,
    )
