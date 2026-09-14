"""ScrapeGraph provider (the prototype's scraping engine)."""

from __future__ import annotations

from typing import Any

from pydantic import SecretStr, ValidationError

from src.schemas.products import ScrapedProduct
from src.stores.scraping.ScraperEnums import ScraperProvider
from src.stores.scraping.ScraperInterface import ScraperInterface
from src.utils.exceptions import (
    ConfigurationError,
    ProviderError,
    ProviderResponseError,
    ScrapingError,
    classify_provider_error,
)
from src.utils.logging import get_logger
from src.utils.retry import RetryPolicy, run_with_retry

__all__ = ["ScrapeGraphProvider"]

logger = get_logger(__name__)

_EXTRACTION_PROMPT = (
    "Extract the product details from this page. "
    "Focus on the main product: title, main image URL, canonical product URL, "
    "current price, original price before discount (if any), discount percentage "
    "(if any), currency, and the most important comparable specifications."
)


class ScrapeGraphProvider(ScraperInterface):
    """ScrapeGraphAI backed product extraction."""

    provider = ScraperProvider.SCRAPEGRAPH

    def __init__(
        self,
        api_key: SecretStr | str | None,
        *,
        timeout_seconds: float = 60.0,
        max_retries: int = 3,
        retry_backoff_seconds: float = 0.5,
    ) -> None:
        key = api_key.get_secret_value() if isinstance(api_key, SecretStr) else api_key
        if not key or not key.strip():
            raise ConfigurationError(
                "SCRAPEGRAPH_API_KEY is required for SCRAPER_PROVIDER=scrapegraph",
                context={"provider": self.provider.value},
            )

        self._api_key = key.strip()
        self._timeout = timeout_seconds
        self._retry_policy = RetryPolicy(attempts=max_retries, backoff_seconds=retry_backoff_seconds)
        self._client: Any | None = None

    def describe(self) -> dict[str, Any]:
        return {"provider": self.provider.value, "timeout_seconds": self._timeout}

    def extract_product(self, url: str) -> ScrapedProduct:
        if not url or not url.strip():
            raise ScrapingError("A page URL is required for extraction", provider=self.provider.value)

        client = self._client_instance()

        def _call() -> Any:
            try:
                return client.extract(
                    prompt=_EXTRACTION_PROMPT,
                    url=url,
                    schema=ScrapedProduct.model_json_schema(),
                )
            except Exception as error:
                raise classify_provider_error(
                    error,
                    provider=self.provider.value,
                    operation="extract_product",
                    context={"url": url},
                ) from error

        try:
            result = run_with_retry(
                _call,
                self._retry_policy,
                description="scraping.scrapegraph",
            )
        except ProviderError as error:
            raise ScrapingError(str(error), context=dict(error.context), cause=error) from error

        return self._map_result(result, url=url)

    # ------------------------------------------------------------- internals
    def _client_instance(self) -> Any:
        if self._client is None:
            from scrapegraph_py import ScrapeGraphAI

            self._client = ScrapeGraphAI(api_key=self._api_key)
        return self._client

    def _map_result(self, result: Any, *, url: str) -> ScrapedProduct:
        error = getattr(result, "error", None)
        data = getattr(result, "data", None)

        if error:
            raise ScrapingError(
                f"ScrapeGraph returned an error for the page: {error}",
                provider=self.provider.value,
                context={"url": url},
            )
        if data is None:
            raise ScrapingError(
                "ScrapeGraph returned no data for the page",
                provider=self.provider.value,
                context={"url": url},
            )

        json_data = getattr(data, "json_data", None)
        if not isinstance(json_data, dict):
            raise ProviderResponseError(
                "ScrapeGraph response did not contain a JSON object",
                provider=self.provider.value,
                context={"url": url, "payload_type": type(json_data).__name__},
            )

        # Trust the page URL we asked for over whatever the page reports.
        payload = dict(json_data)
        payload["page_url"] = url
        payload.setdefault("product_url", url)

        try:
            return ScrapedProduct.model_validate(payload)
        except ValidationError as error:
            raise ScrapingError(
                "Scraped payload did not satisfy the product schema",
                provider=self.provider.value,
                context={"url": url, "validation_errors": error.error_count()},
                cause=error,
            ) from error
