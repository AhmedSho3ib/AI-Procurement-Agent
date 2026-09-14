"""Product/search filtering and price aggregation rules."""

from __future__ import annotations

import pytest

from src.schemas.products import PriceSummary, Product, ScrapedProduct
from src.schemas.search import SearchResult
from src.services.procurement_service.rules import product_filtering as rules


def _result(url: str, score: float, title: str = "Product") -> SearchResult:
    return SearchResult(title=title, url=url, score=score, content="snippet", search_query="q")


class TestFilterSearchResults:
    def test_keeps_only_high_scoring_product_pages(self) -> None:
        results = [
            _result("https://www.amazon.eg/DeLonghi/dp/B00400OMU0", 0.98),
            _result("https://www.amazon.eg/blog/best-coffee", 0.99),
            _result("https://www.amazon.eg/Philips/dp/B082Y45678", 0.05),
            _result("https://www.amazon.eg/login", 0.97),
        ]
        outcome = rules.filter_search_results(results, score_threshold=0.10)

        assert [item.url for item in outcome.kept] == ["https://www.amazon.eg/DeLonghi/dp/B00400OMU0"]
        assert outcome.rejected_count == 3
        assert len(outcome.reasons) == 3
        assert any("score threshold" in reason for reason in outcome.reasons)
        assert any("blog" in reason or "product page" in reason for reason in outcome.reasons)

    def test_enforces_domain_allow_list(self) -> None:
        results = [
            _result("https://www.amazon.eg/DeLonghi/dp/B00400OMU0", 0.98),
            _result("https://www.amazon.com/DeLonghi/dp/B00400OMU0", 0.98),
        ]
        outcome = rules.filter_search_results(
            results,
            score_threshold=0.10,
            allowed_domains=frozenset({"amazon.eg"}),
        )
        assert len(outcome.kept) == 1
        assert outcome.kept[0].url.endswith("amazon.eg/DeLonghi/dp/B00400OMU0")

    def test_empty_input_is_not_an_error(self) -> None:
        outcome = rules.filter_search_results([], score_threshold=0.1)
        assert outcome.kept == []
        assert outcome.reasons == []


class TestFilterProducts:
    def test_drops_products_without_price_title_or_valid_url(self, product: Product) -> None:
        broken_no_price = Product(**{**product.model_dump(), "product_current_price": 0.0})
        broken_no_title = Product(**{**product.model_dump(), "product_title": ""})
        broken_url = Product(**{**product.model_dump(), "product_url": "", "page_url": "not-a-url"})

        outcome = rules.filter_products([product, broken_no_price, broken_no_title, broken_url])

        assert outcome.kept == [product]
        assert outcome.rejected_count == 3
        joined = " ".join(outcome.reasons)
        assert "price" in joined
        assert "title" in joined
        assert "invalid product URL" in joined

    def test_rejects_a_product_with_no_url_at_all(self, product: Product) -> None:
        # ``model_construct`` bypasses schema validation to exercise the defensive
        # branch that protects the pipeline from upstream contract violations.
        no_url = Product.model_construct(**{**product.model_dump(), "product_url": "", "page_url": ""})
        outcome = rules.filter_products([no_url])
        assert outcome.kept == []
        assert "missing product URL" in outcome.reasons[0]

    def test_accepts_scraped_products(self, scraped_product: ScrapedProduct) -> None:
        outcome = rules.filter_products([scraped_product])
        assert len(outcome.kept) == 1


class TestRankingAndSelection:
    def test_ranks_by_rank_then_price(self, product: Product) -> None:
        cheap_low_rank = Product(
            **{**product.model_dump(), "product_current_price": 100.0, "agent_recommendation_rank": 2}
        )
        pricey_high_rank = Product(
            **{**product.model_dump(), "product_current_price": 900.0, "agent_recommendation_rank": 5}
        )
        ranked = rules.rank_products([cheap_low_rank, pricey_high_rank])
        assert ranked[0].agent_recommendation_rank == 5

    def test_selects_top_n_and_reports_the_dropped_rest(self, product: Product) -> None:
        products = [
            Product(
                page_url=f"https://www.amazon.eg/p/dp/B00400OM{i}",
                product_url=f"https://www.amazon.eg/p/dp/B00400OM{i}",
                product_title=f"Product {i}",
                product_current_price=100.0 + i,
                agent_recommendation_rank=3,
            )
            for i in range(5)
        ]
        selected, dropped = rules.select_top_products(products, 2)
        assert len(selected) == 2
        assert len(dropped) == 3

    def test_top_n_is_at_least_one(self, product: Product) -> None:
        selected, dropped = rules.select_top_products([product], 0)
        assert len(selected) == 1
        assert dropped == []


class TestPriceSummary:
    def test_builds_statistics_from_priced_products(self) -> None:
        products = [
            Product(
                page_url=f"https://www.amazon.eg/p/dp/B00400O{i}",
                product_url=f"https://www.amazon.eg/p/dp/B00400O{i}",
                product_title=f"P{i}",
                product_current_price=price,
                product_currency="EGP",
            )
            for i, price in enumerate([100.0, 200.0, 300.0])
        ]
        summary = rules.build_price_summary(products)

        assert isinstance(summary, PriceSummary)
        assert summary.product_count == 3
        assert summary.min_price == 100.0
        assert summary.max_price == 300.0
        assert summary.average_price == 200.0
        assert summary.median_price == 200.0
        assert summary.currency == "EGP"
        assert summary.cheapest_product_title == "P0"
        assert summary.most_expensive_product_title == "P2"

    def test_returns_empty_summary_without_priced_products(self) -> None:
        zero_priced = Product(page_url="https://x.com/p/1", product_current_price=0.0)
        summary = rules.build_price_summary([zero_priced])
        assert summary.product_count == 0
        assert summary.min_price is None

    def test_ignores_products_without_price(self) -> None:
        priced = Product(page_url="https://x.com/p/1", product_current_price=50.0)
        unpriced = Product(page_url="https://x.com/p/2", product_current_price=0.0)
        summary = rules.build_price_summary([priced, unpriced])
        assert summary.product_count == 1

    @pytest.mark.parametrize("prices", [[10.0], [5.0, 15.0]])
    def test_min_and_max_are_consistent(self, prices: list[float]) -> None:
        products = [
            Product(page_url=f"https://x.com/p/{i}", product_current_price=price)
            for i, price in enumerate(prices)
        ]
        summary = rules.build_price_summary(products)
        assert summary.min_price == min(prices)
        assert summary.max_price == max(prices)
