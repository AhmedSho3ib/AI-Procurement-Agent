"""Tavily and ScrapeGraph provider adapters, tested against fake SDK clients.

No network access and no credentials: the SDK client is replaced through the
providers' documented client seam.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.schemas.products import ScrapedProduct
from src.stores.scraping.providers.ScrapeGraphProvider import ScrapeGraphProvider
from src.stores.search.providers.TavilyProvider import TavilyProvider
from src.utils.exceptions import (
    ProviderAuthError,
    ProviderResponseError,
    ScrapingError,
    SearchProviderError,
)


class FakeTavilyClient:
    def __init__(self, payload: Any = None, error: Exception | None = None) -> None:
        self._payload = payload if payload is not None else {"results": []}
        self._error = error
        self.calls: list[dict[str, Any]] = []

    def search(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return self._payload


class FakeScrapeGraphClient:
    def __init__(self, payload: Any = None, error: Exception | None = None) -> None:
        self._payload = payload
        self._error = error
        self.calls: list[dict[str, Any]] = []

    def extract(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return self._payload


class FakeData:
    def __init__(self, json_data: Any) -> None:
        self.json_data = json_data


class FakeResult:
    def __init__(self, json_data: Any = None, error: Any = None) -> None:
        self.data = FakeData(json_data) if json_data is not None else None
        self.error = error


def _tavily(fake: FakeTavilyClient) -> TavilyProvider:
    provider = TavilyProvider(api_key="tvly-test", max_retries=1)
    provider._client = fake  # documented client seam used by tests
    return provider


def _scrapegraph(fake: FakeScrapeGraphClient) -> ScrapeGraphProvider:
    provider = ScrapeGraphProvider(api_key="sgai-test", max_retries=1)
    provider._client = fake
    return provider


class TestTavilyProvider:
    def test_maps_provider_payload_into_schema(self) -> None:
        client = FakeTavilyClient(
            {
                "results": [
                    {
                        "title": "De'Longhi Magnifica S",
                        "url": "https://www.amazon.eg/dp/B00400OMU0",
                        "content": "Automatic coffee machine",
                        "score": 0.98,
                    }
                ]
            }
        )
        results = _tavily(client).search("coffee machine", max_results=5)

        assert len(results) == 1
        assert results[0].search_query == "coffee machine"
        assert results[0].score == 0.98
        assert client.calls[0]["query"] == "coffee machine"
        assert client.calls[0]["max_results"] == 5

    def test_clamps_out_of_range_scores(self) -> None:
        client = FakeTavilyClient({"results": [{"url": "https://x.com/p/1", "score": 7.5}]})
        assert _tavily(client).search("q")[0].score == 1.0
        client = FakeTavilyClient({"results": [{"url": "https://x.com/p/1", "score": "not-a-number"}]})
        assert _tavily(client).search("q")[0].score == 0.0

    def test_skips_malformed_rows_without_failing_the_batch(self) -> None:
        client = FakeTavilyClient(
            {
                "results": [
                    "not a dict",
                    {"url": "https://x.com/p/1", "title": "fine", "score": 0.9},
                    {"url": "", "title": "no url"},
                ]
            }
        )
        results = _tavily(client).search("q")
        assert [item.url for item in results] == ["https://x.com/p/1"]

    def test_rejects_a_non_object_payload(self) -> None:
        with pytest.raises(ProviderResponseError):
            _tavily(FakeTavilyClient(payload=["unexpected"])).search("q")

    def test_rejects_a_non_list_results_field(self) -> None:
        with pytest.raises(ProviderResponseError):
            _tavily(FakeTavilyClient(payload={"results": "nope"})).search("q")

    def test_rejects_an_empty_query_without_calling_the_provider(self) -> None:
        client = FakeTavilyClient()
        with pytest.raises(SearchProviderError):
            _tavily(client).search("   ")
        assert client.calls == []

    def test_wraps_provider_failures_in_a_domain_error(self) -> None:
        client = FakeTavilyClient(error=RuntimeError("Unauthorized: invalid api key"))
        with pytest.raises(SearchProviderError) as error:
            _tavily(client).search("q")
        assert "provider" in error.value.context

    def test_auth_errors_are_classified_as_permanent(self) -> None:
        class HttpError(Exception):
            status_code = 401

        client = FakeTavilyClient(error=HttpError("nope"))
        with pytest.raises(SearchProviderError) as error:
            _tavily(client).search("q")
        # The domain error wraps the classified provider error, which is what the
        # retry policy and the API error handler act on.
        assert isinstance(error.value.__cause__, ProviderAuthError)
        assert error.value.context["status_code"] == 401

    def test_requires_credentials(self) -> None:
        from src.utils.exceptions import ConfigurationError

        with pytest.raises(ConfigurationError):
            TavilyProvider(api_key="")

    def test_describe_is_secret_free(self) -> None:
        described = _tavily(FakeTavilyClient()).describe()
        assert set(described) == {"provider", "search_depth", "timeout_seconds", "include_domains"}
        assert "tvly-test" not in str(described)


class TestScrapeGraphProvider:
    def test_maps_provider_payload_into_schema(self) -> None:
        url = "https://www.amazon.eg/dp/B00400OMU0"
        client = FakeScrapeGraphClient(
            FakeResult(
                json_data={
                    "product_title": "De'Longhi Magnifica S",
                    "product_current_price": 24999.0,
                    "product_specs": [{"specification_name": "Type", "specification_value": "Automatic"}],
                }
            )
        )
        product = _scrapegraph(client).extract_product(url)

        assert isinstance(product, ScrapedProduct)
        assert product.page_url == url
        assert product.product_url == url
        assert product.product_current_price == 24999.0
        assert client.calls[0]["url"] == url
        assert client.calls[0]["schema"]["title"] == ScrapedProduct.__name__

    def test_provider_error_field_becomes_a_scraping_error(self) -> None:
        client = FakeScrapeGraphClient(FakeResult(error="rate limited upstream"))
        with pytest.raises(ScrapingError):
            _scrapegraph(client).extract_product("https://x.com/p/1")

    def test_missing_data_becomes_a_scraping_error(self) -> None:
        client = FakeScrapeGraphClient(None)
        with pytest.raises(ScrapingError):
            _scrapegraph(client).extract_product("https://x.com/p/1")

    def test_non_object_json_data_is_a_provider_response_error(self) -> None:
        client = FakeScrapeGraphClient(FakeResult(json_data=["nope"]))
        with pytest.raises(ProviderResponseError):
            _scrapegraph(client).extract_product("https://x.com/p/1")

    def test_schema_violations_become_scraping_errors(self) -> None:
        client = FakeScrapeGraphClient(FakeResult(json_data={"product_current_price": "not a price"}))
        with pytest.raises(ScrapingError) as error:
            _scrapegraph(client).extract_product("https://x.com/p/1")
        assert "validation_errors" in error.value.context

    def test_rejects_an_empty_url(self) -> None:
        client = FakeScrapeGraphClient()
        with pytest.raises(ScrapingError):
            _scrapegraph(client).extract_product("")
        assert client.calls == []

    def test_sdk_exceptions_are_classified(self) -> None:
        class HttpError(Exception):
            status_code = 401

        client = FakeScrapeGraphClient(error=HttpError("unauthorized client detected"))
        with pytest.raises(ScrapingError) as error:
            _scrapegraph(client).extract_product("https://x.com/p/1")
        assert error.value.context.get("provider") == "scrapegraph"
        assert isinstance(error.value.__cause__, ProviderAuthError)

    def test_requires_credentials(self) -> None:
        from src.utils.exceptions import ConfigurationError

        with pytest.raises(ConfigurationError):
            ScrapeGraphProvider(api_key="")

    def test_provider_auth_error_is_never_retried(self) -> None:
        class HttpError(Exception):
            status_code = 403

        provider = ScrapeGraphProvider(api_key="sgai-test", max_retries=3, retry_backoff_seconds=0)
        provider._client = FakeScrapeGraphClient(error=HttpError("forbidden"))
        with pytest.raises(ScrapingError):
            provider.extract_product("https://x.com/p/1")

    def test_transient_errors_are_retried_then_reported(self) -> None:
        class HttpError(Exception):
            status_code = 429

        provider = ScrapeGraphProvider(api_key="sgai-test", max_retries=2, retry_backoff_seconds=0)
        attempts: list[int] = []

        class CountingClient:
            def extract(self, **kwargs: Any) -> Any:
                attempts.append(1)
                raise HttpError("rate limited")

        provider._client = CountingClient()
        with pytest.raises(ScrapingError):
            provider.extract_product("https://x.com/p/1")
        assert len(attempts) == 2

    def test_missing_api_key_is_reported_not_faked(self) -> None:
        from src.utils.exceptions import ConfigurationError

        with pytest.raises(ConfigurationError) as error:
            ScrapeGraphProvider(api_key="   ")
        assert error.value.code == "configuration_error"
