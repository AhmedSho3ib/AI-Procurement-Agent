"""Deterministic business rules.

The rule of thumb for this package: if a decision can be made with code, it is
made here and not by a language model. These functions are pure, fast, fully
testable and never call an external system.
"""

from src.services.procurement_service.rules.deduplication import (
    deduplicate_products,
    deduplicate_search_results,
    product_identity,
    search_result_identity,
)
from src.services.procurement_service.rules.product_filtering import (
    FilterOutcome,
    build_price_summary,
    filter_products,
    filter_search_results,
    rank_products,
    select_top_products,
)
from src.services.procurement_service.rules.url_validation import (
    UrlValidationResult,
    extract_domain,
    extract_product_identifier,
    is_allowed_domain,
    is_likely_product_page,
    is_suspicious_url,
    is_valid_url,
    normalize_domain,
    normalize_url,
    validate_url,
)

__all__ = [
    "FilterOutcome",
    "UrlValidationResult",
    "build_price_summary",
    "deduplicate_products",
    "deduplicate_search_results",
    "extract_domain",
    "extract_product_identifier",
    "filter_products",
    "filter_search_results",
    "is_allowed_domain",
    "is_likely_product_page",
    "is_suspicious_url",
    "is_valid_url",
    "normalize_domain",
    "normalize_url",
    "product_identity",
    "rank_products",
    "search_result_identity",
    "select_top_products",
    "validate_url",
]
