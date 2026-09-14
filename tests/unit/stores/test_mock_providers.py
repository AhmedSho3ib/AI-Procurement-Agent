"""Mock provider behaviour: determinism is a feature, not an accident."""

from __future__ import annotations

import pytest

from src.schemas.products import Products, ScrapedProduct
from src.schemas.reports import ProcurementReport
from src.schemas.search import SearchResult, SuggestedSearchQueries
from src.stores.llm.providers.MockProvider import MockProvider as MockLLM
from src.stores.scraping.providers.MockProvider import MockProvider as MockScraper
from src.stores.search.providers.MockProvider import MockProvider as MockSearch
from src.utils.exceptions import ProviderResponseError, ScrapingError


class TestMockSearchProvider:
    def test_returns_the_requested_number_of_valid_product_urls(self) -> None:
        results = MockSearch().search("coffee machine", max_results=3)
        assert len(results) == 3
        assert all(isinstance(item, SearchResult) for item in results)
        assert all(item.url.startswith("https://") for item in results)
        assert all(item.search_query == "coffee machine" for item in results)

    def test_scores_decrease_monotonically(self) -> None:
        scores = [item.score for item in MockSearch().search("coffee machine", max_results=4)]
        assert scores == sorted(scores, reverse=True)
        assert all(0.0 <= score <= 1.0 for score in scores)

    def test_is_deterministic(self) -> None:
        assert MockSearch().search("q", max_results=2) == MockSearch().search("q", max_results=2)

    def test_respects_the_result_cap(self) -> None:
        assert len(MockSearch(results_per_query=2).search("q", max_results=10)) == 2


class TestMockScraperProvider:
    def test_returns_a_stable_product_per_url(self) -> None:
        scraper = MockScraper()
        first = scraper.extract_product("https://www.amazon.eg/products/coffee-machine-1.html")
        second = scraper.extract_product("https://www.amazon.eg/products/coffee-machine-1.html")
        assert first == second
        assert isinstance(first, ScrapedProduct)
        assert first.product_current_price > 0
        assert first.product_currency == "EGP"
        assert first.product_specs

    def test_different_urls_produce_different_products(self) -> None:
        scraper = MockScraper()
        first = scraper.extract_product("https://www.amazon.eg/products/a-1.html")
        second = scraper.extract_product("https://www.jumia.com.eg/products/b-2.html")
        assert first.product_title != second.product_title
        assert first.product_image_url != second.product_image_url
        assert first.product_current_price > 0 and second.product_current_price > 0

    def test_rejects_an_empty_url(self) -> None:
        with pytest.raises(ScrapingError):
            MockScraper().extract_product("")


class TestMockLLMProvider:
    def test_generates_queries_from_the_request_context(self) -> None:
        provider = MockLLM()
        queries = provider.generate_structured(
            "ignored prompt",
            SuggestedSearchQueries,
            context={
                "request": {
                    "product": "coffee machine",
                    "websites": ["www.amazon.eg", "www.jumia.com.eg"],
                    "max_queries": 3,
                }
            },
        )
        assert len(queries.queries) == 3
        assert all("coffee machine" in query for query in queries.queries)
        assert {query.split("site:")[-1] for query in queries.queries} == {
            "www.amazon.eg",
            "www.jumia.com.eg",
        }

    def test_accepts_websites_as_a_comma_separated_string(self) -> None:
        queries = MockLLM().generate_structured(
            "prompt",
            SuggestedSearchQueries,
            context={"websites": "www.amazon.eg, www.jumia.com.eg", "max_queries": 2},
        )
        assert all("site:" in query for query in queries.queries)

    def test_ranks_scraped_products_by_price(self, scraped_product: ScrapedProduct) -> None:
        cheaper = ScrapedProduct(**{**scraped_product.model_dump(), "product_current_price": 100.0})
        pricier = ScrapedProduct(**{**scraped_product.model_dump(), "product_current_price": 900.0})

        ranked = MockLLM().generate_structured(
            "prompt",
            Products,
            context={"scraped_products": [pricier.model_dump(), cheaper.model_dump()]},
        )

        assert [product.product_current_price for product in ranked.products] == [100.0, 900.0]
        assert ranked.products[0].agent_recommendation_rank >= ranked.products[1].agent_recommendation_rank
        assert ranked.products[0].agent_recommendation_notes

    def test_writes_report_content_using_the_supplied_products(self) -> None:
        from src.schemas.products import Product

        product = Product(
            page_url="https://www.amazon.eg/dp/B00400OMU0",
            product_title="De'Longhi Magnifica S",
            product_current_price=24999.0,
            product_currency="EGP",
            agent_recommendation_rank=5,
        )
        report = MockLLM().generate_structured(
            "prompt",
            ProcurementReport,
            context={
                "product": "coffee machine",
                "company_name": "Rankyx",
                "websites": ["www.amazon.eg"],
                "country": "Egypt",
                "products": [product.model_dump()],
            },
        )
        assert "coffee machine" in report.title
        assert "De'Longhi Magnifica S" in report.findings
        assert report.recommendations

    def test_unsupported_response_model_is_rejected_loudly(self) -> None:
        from pydantic import BaseModel

        class Unsupported(BaseModel):
            value: str

        with pytest.raises(ProviderResponseError) as error:
            MockLLM().generate_structured("prompt", Unsupported)
        assert "supported" in error.value.context

    def test_generate_returns_text(self) -> None:
        assert MockLLM().generate("hello").startswith("[mock-llm]")

    def test_model_name_is_stable(self) -> None:
        assert MockLLM().model_name == "mock-llm"
