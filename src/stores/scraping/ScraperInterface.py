"""The web-scraping provider contract.

A scraper's only job is turning one URL into one strictly validated
:class:`~src.schemas.products.ScrapedProduct`. Ranking and recommendation notes
are *reasoning* outputs owned by the extraction agent, not by a scraper, which is
why they are not part of this contract.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from src.schemas.products import ScrapedProduct
from src.stores.scraping.ScraperEnums import ScraperProvider

__all__ = ["ScraperInterface"]


class ScraperInterface(ABC):
    """Extracts structured product data from a product page."""

    provider: ScraperProvider

    @abstractmethod
    def extract_product(self, url: str) -> ScrapedProduct:
        """Extract product data from ``url``.

        Raises:
            ScrapingError: the page could not be scraped or produced no usable data.
            ProviderAuthError: credential rejected (permanent).
            ProviderRateLimitError | ProviderTimeoutError | ProviderUnavailableError: transient.
        """

    def describe(self) -> dict[str, Any]:
        """Secret-free provider information for health checks and traces."""
        return {"provider": self.provider.value}
