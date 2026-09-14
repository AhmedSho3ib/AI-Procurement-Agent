"""Scraping provider identifiers."""

from __future__ import annotations

from enum import StrEnum

__all__ = ["ScraperProvider"]


class ScraperProvider(StrEnum):
    """Supported web scraping providers."""

    SCRAPEGRAPH = "scrapegraph"
    MOCK = "mock"
