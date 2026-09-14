"""Product and search schema tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.schemas.products import Product, Products, ProductSpecification, ScrapedProduct
from src.schemas.search import SearchResult, SearchResults, SuggestedSearchQueries


class TestSearchSchemas:
    def test_clamps_score_bounds(self) -> None:
        with pytest.raises(ValidationError):
            SearchResult(url="https://x.com/p/1", score=1.4)
        with pytest.raises(ValidationError):
            SearchResult(url="https://x.com/p/1", score=-0.1)

    def test_requires_a_url(self) -> None:
        with pytest.raises(ValidationError):
            SearchResult(url="")

    def test_accepts_malformed_url_strings_for_rule_level_filtering(self) -> None:
        # URL quality is a deterministic business rule, not a schema concern, so
        # syntactically odd URLs must still deserialise.
        result = SearchResult(url="not a url at all", title="Broken", score=0.5)
        assert result.url == "not a url at all"

    def test_suggested_queries_requires_at_least_one_query(self) -> None:
        with pytest.raises(ValidationError):
            SuggestedSearchQueries(queries=[])

    def test_search_results_wrapper_reports_length(self) -> None:
        results = SearchResults(results=[SearchResult(url="https://x.com/p/1")])
        assert len(results) == 1


class TestProductSchemas:
    def test_scraped_product_allows_missing_optional_fields(self) -> None:
        product = ScrapedProduct(page_url="https://x.com/p/1")
        assert product.product_image_url is None
        assert product.product_original_price is None
        assert product.product_specs == []

    def test_rejects_negative_price(self) -> None:
        with pytest.raises(ValidationError):
            ScrapedProduct(page_url="https://x.com/p/1", product_current_price=-5)

    def test_rejects_discount_above_100_percent(self) -> None:
        with pytest.raises(ValidationError):
            ScrapedProduct(page_url="https://x.com/p/1", product_discount_percentage=120)

    def test_limits_specifications_to_five(self) -> None:
        specs = [ProductSpecification(specification_name=f"s{i}", specification_value="v") for i in range(6)]
        with pytest.raises(ValidationError):
            ScrapedProduct(page_url="https://x.com/p/1", product_specs=specs)

    def test_product_rank_bounds(self) -> None:
        with pytest.raises(ValidationError):
            Product(page_url="https://x.com/p/1", agent_recommendation_rank=6)

    def test_effective_url_prefers_product_url(self, product: Product) -> None:
        assert product.effective_url == product.product_url

    def test_effective_url_falls_back_to_page_url(self) -> None:
        product = Product(page_url="https://x.com/p/1", product_url="")
        assert product.effective_url == "https://x.com/p/1"

    def test_products_wrapper_validates_from_dict(self, scraped_product: ScrapedProduct) -> None:
        payload = {"products": [{**scraped_product.model_dump(), "agent_recommendation_rank": 3}]}
        products = Products.model_validate(payload)
        assert products.products[0].agent_recommendation_rank == 3
