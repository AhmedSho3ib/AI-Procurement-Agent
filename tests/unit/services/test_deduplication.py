"""Deduplication rules, including the prototype's real captured search results."""

from __future__ import annotations

from typing import Any

from src.schemas.products import Product
from src.schemas.search import SearchResult
from src.services.procurement_service.rules import deduplication as rules


def _product(url: str, rank: int, price: float = 100.0, product_url: str | None = None) -> Product:
    return Product(
        page_url=url,
        product_url=product_url if product_url is not None else url,
        product_title=f"Product at {url}",
        product_current_price=price,
        agent_recommendation_rank=rank,
    )


class TestSearchResultDeduplication:
    def test_collapses_tracking_variants_of_the_same_url(self) -> None:
        results = [
            SearchResult(url="https://www.amazon.eg/dp/B00400OMU0?tag=affiliate", score=0.9),
            SearchResult(url="https://www.amazon.eg/dp/B00400OMU0?utm_source=news", score=0.8),
            SearchResult(url="https://www.amazon.eg/dp/B00400OMU0", score=0.7),
        ]
        deduplicated = rules.deduplicate_search_results(results)
        assert len(deduplicated) == 1
        assert deduplicated[0].score == 0.9

    def test_keeps_distinct_products(self) -> None:
        results = [
            SearchResult(url="https://www.amazon.eg/dp/B00400OMU0", score=0.9),
            SearchResult(url="https://www.amazon.eg/dp/B082Y45678", score=0.85),
        ]
        assert len(rules.deduplicate_search_results(results)) == 2

    def test_keeps_the_first_position_but_the_highest_score(self) -> None:
        results = [
            SearchResult(url="https://www.jumia.com.eg/a-1.html", score=0.5),
            SearchResult(url="https://www.amazon.eg/dp/B00400OMU0", score=0.9),
            SearchResult(url="https://www.jumia.com.eg/a-1.html?ref=x", score=0.6),
        ]
        deduplicated = rules.deduplicate_search_results(results)
        assert len(deduplicated) == 2
        # The survivor keeps the position of the first occurrence...
        assert deduplicated[0].url == "https://www.jumia.com.eg/a-1.html?ref=x"
        # ...and carries the best score seen for that identity.
        assert deduplicated[0].score == 0.6

    def test_prototype_results_are_unchanged_by_deduplication(
        self, prototype_search_results: dict[str, Any]
    ) -> None:
        results = [SearchResult.model_validate(item) for item in prototype_search_results["results"]]
        assert len(rules.deduplicate_search_results(results)) == len(results)


class TestProductDeduplication:
    def test_collapses_the_same_marketplace_product_id(self) -> None:
        products = [
            _product("https://www.amazon.eg/DeLonghi-A/dp/B00400OMU0", rank=3, price=120.0),
            _product("https://www.amazon.eg/DeLonghi-B/dp/B00400OMU0?ref=xyz", rank=5, price=110.0),
        ]
        deduplicated = rules.deduplicate_products(products)
        assert len(deduplicated) == 1
        assert deduplicated[0].agent_recommendation_rank == 5

    def test_prefers_lower_price_when_ranks_tie(self) -> None:
        products = [
            _product("https://www.jumia.com.eg/chair-1.html", rank=4, price=500.0),
            _product("https://www.jumia.com.eg/chair-1.html?utm_source=x", rank=4, price=450.0),
        ]
        deduplicated = rules.deduplicate_products(products)
        assert len(deduplicated) == 1
        assert deduplicated[0].product_current_price == 450.0

    def test_keeps_the_same_product_on_different_marketplaces(self) -> None:
        products = [
            _product("https://www.amazon.eg/DeLonghi/dp/B00400OMU0", rank=4),
            _product("https://www.noon.com/egypt-en/deonghi/N40636566A/p/", rank=4),
        ]
        assert len(rules.deduplicate_products(products)) == 2

    def test_identity_uses_product_identifier_when_available(self) -> None:
        assert rules.product_identity(_product("https://www.amazon.eg/x/dp/B00400OMU0", rank=1)) == (
            "amazon.eg:B00400OMU0"
        )

    def test_identity_falls_back_to_normalized_url(self) -> None:
        identity = rules.product_identity(_product("https://example.com/product/a?utm_source=x", rank=1))
        assert identity == "https://example.com/product/a"
