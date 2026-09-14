"""Deterministic, offline search provider used for tests, CI and local demos."""

from __future__ import annotations

from collections.abc import Sequence

from src.schemas.search import SearchResult
from src.stores.search.SearchEnums import SearchProvider
from src.stores.search.SearchInterface import SearchInterface

__all__ = ["MockProvider"]

_DEFAULT_DOMAINS: tuple[str, ...] = (
    "www.amazon.eg",
    "www.jumia.com.eg",
    "www.noon.com/egypt-en",
)


class MockProvider(SearchInterface):
    """Returns synthetic, deterministic results for any query."""

    provider = SearchProvider.MOCK

    def __init__(
        self,
        domains: Sequence[str] | None = None,
        *,
        results_per_query: int = 3,
        base_score: float = 0.95,
    ) -> None:
        self._domains = (
            tuple(domain for domain in (domains or _DEFAULT_DOMAINS) if domain.strip()) or _DEFAULT_DOMAINS
        )
        self._results_per_query = max(1, results_per_query)
        self._base_score = min(max(base_score, 0.0), 1.0)

    def search(self, query: str, *, max_results: int = 10) -> list[SearchResult]:
        limit = max(1, min(max_results, self._results_per_query))
        slug = _slugify(query) or "product"
        results: list[SearchResult] = []
        for index in range(limit):
            domain = self._domains[index % len(self._domains)]
            score = max(0.0, self._base_score - (index * 0.05))
            results.append(
                SearchResult(
                    title=f"Synthetic listing {index + 1} for '{query}'",
                    url=f"https://{domain}/products/{slug}-{index + 1}.html",
                    content=(
                        f"Synthetic search snippet for query '{query}'. "
                        f"Deterministic mock data - no network call was made."
                    ),
                    score=round(score, 4),
                    search_query=query,
                )
            )
        return results


def _slugify(value: str) -> str:
    cleaned = [char.lower() if char.isalnum() else "-" for char in value.strip()]
    slug = "".join(cleaned)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-")[:80]
