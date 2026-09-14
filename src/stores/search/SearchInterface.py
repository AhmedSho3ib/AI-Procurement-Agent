"""The search provider contract.

Providers return *raw* results only. Determining whether a URL is a valid,
single-product page is a deterministic business rule and therefore lives in
``src.services.procurement_service.rules``, never here.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from src.schemas.search import SearchResult
from src.stores.search.SearchEnums import SearchProvider

__all__ = ["SearchInterface"]


class SearchInterface(ABC):
    """Executes a search query and returns normalised results."""

    provider: SearchProvider

    @abstractmethod
    def search(self, query: str, *, max_results: int = 10) -> list[SearchResult]:
        """Execute ``query`` and return at most ``max_results`` results.

        Raises:
            ProviderAuthError: credential rejected (permanent).
            ProviderRateLimitError | ProviderTimeoutError | ProviderUnavailableError: transient.
            ProviderResponseError: malformed provider payload (permanent).
        """

    def describe(self) -> dict[str, Any]:
        """Secret-free provider information for health checks and traces."""
        return {"provider": self.provider.value}
