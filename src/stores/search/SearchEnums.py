"""Search provider identifiers."""

from __future__ import annotations

from enum import StrEnum

__all__ = ["SearchProvider"]


class SearchProvider(StrEnum):
    """Supported search providers.

    Adding a provider (SerpAPI, Brave, Bing...) means adding a value here and
    registering the class in :class:`~src.stores.search.SearchFactory`.
    """

    TAVILY = "tavily"
    MOCK = "mock"
