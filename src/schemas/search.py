"""Search related data contracts.

These models mirror the structured output produced by the prototype's
``SignleSearchResult`` / ``AllSearchResults`` models. They are pure data
containers: URL quality rules live in
``src.services.procurement_service.rules.url_validation``.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["SearchResult", "SearchResults", "SuggestedSearchQueries"]


class SearchResult(BaseModel):
    """A single search engine hit.

    ``url`` is intentionally typed as ``str`` rather than ``HttpUrl``: suspicious
    or malformed URLs coming back from a search provider are *filtered* by
    deterministic rules instead of failing the whole batch. Rejecting the entire
    response because one of ten hits is malformed would lose nine valid results.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(default="", max_length=500, description="Page title as returned by the provider")
    url: str = Field(min_length=1, max_length=2048, description="The page URL")
    content: str = Field(default="", max_length=8000, description="Provider supplied snippet/summary")
    score: float = Field(default=0.0, ge=0.0, le=1.0, description="Provider relevance confidence (0-1)")
    search_query: str = Field(default="", max_length=300, description="Query that produced this result")


class SearchResults(BaseModel):
    """Collection wrapper used as the search agent's structured output."""

    results: list[SearchResult] = Field(default_factory=list)

    def __len__(self) -> int:
        return len(self.results)


class SuggestedSearchQueries(BaseModel):
    """Structured output of the search-queries recommendation agent."""

    model_config = ConfigDict(str_strip_whitespace=True)

    queries: list[str] = Field(
        min_length=1,
        max_length=20,
        description="Suggested search queries to pass to the search engine",
    )

    def __len__(self) -> int:
        return len(self.queries)
