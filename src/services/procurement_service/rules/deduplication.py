"""Deterministic deduplication rules.

Marketplaces expose the same product under many URLs (tracking parameters,
regional hosts, mobile variants, mirrors). Deduplication happens on a normalised
URL and, when a marketplace-native identifier can be recognised, on that
identifier too.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from src.schemas.products import Product, ScrapedProduct
from src.schemas.search import SearchResult
from src.services.procurement_service.rules.url_validation import (
    extract_product_identifier,
    normalize_domain,
    normalize_url,
)
from src.utils.logging import get_logger

__all__ = [
    "deduplicate_products",
    "deduplicate_search_results",
    "product_identity",
    "search_result_identity",
]

logger = get_logger(__name__)


def search_result_identity(result: SearchResult) -> str:
    """Stable identity for a search result."""
    return normalize_url(result.url)


def product_identity(product: Product | ScrapedProduct) -> str:
    """Stable identity for a product: native identifier when available, else URL.

    The marketplace is included so the same ASIN on two different regional
    Amazon storefronts is not collapsed into one offer.
    """
    url = product.product_url or product.page_url
    identifier = extract_product_identifier(url)
    domain = normalize_domain(url)
    if identifier:
        return f"{domain}:{identifier}"
    return normalize_url(url)


def deduplicate_search_results(results: Sequence[SearchResult]) -> list[SearchResult]:
    """Keep the highest scoring result per normalised URL.

    Input order is preserved for the surviving entries so that downstream
    reporting stays stable and reproducible.
    """
    best: dict[str, SearchResult] = {}
    order: list[str] = []
    for result in results:
        identity = search_result_identity(result)
        current = best.get(identity)
        if current is None:
            best[identity] = result
            order.append(identity)
            continue
        if result.score > current.score:
            best[identity] = result
    survivors = [best[identity] for identity in order]
    if len(survivors) < len(results):
        logger.debug(
            "search_results_deduplicated",
            extra={"received": len(results), "kept": len(survivors)},
        )
    return survivors


def deduplicate_products(products: Iterable[Product]) -> list[Product]:
    """Keep one offer per product identity, preferring the better ranked offer."""
    best: dict[str, Product] = {}
    for product in products:
        identity = product_identity(product)
        current = best.get(identity)
        if current is None or _is_better(product, current):
            best[identity] = product
    return list(best.values())


def _is_better(candidate: Product, incumbent: Product) -> bool:
    if candidate.agent_recommendation_rank != incumbent.agent_recommendation_rank:
        return candidate.agent_recommendation_rank > incumbent.agent_recommendation_rank
    return candidate.product_current_price < incumbent.product_current_price
