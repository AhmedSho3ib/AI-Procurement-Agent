"""Tavily search provider (the prototype's search engine)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import SecretStr

from src.schemas.search import SearchResult
from src.stores.search.SearchEnums import SearchProvider
from src.stores.search.SearchInterface import SearchInterface
from src.utils.exceptions import (
    ConfigurationError,
    ProviderError,
    ProviderResponseError,
    SearchProviderError,
    classify_provider_error,
)
from src.utils.logging import get_logger
from src.utils.retry import RetryPolicy, run_with_retry

__all__ = ["TavilyProvider"]

logger = get_logger(__name__)


class TavilyProvider(SearchInterface):
    """Tavily backed search."""

    provider = SearchProvider.TAVILY

    def __init__(
        self,
        api_key: SecretStr | str | None,
        *,
        timeout_seconds: float = 30.0,
        max_retries: int = 3,
        retry_backoff_seconds: float = 0.5,
        include_domains: Sequence[str] | None = None,
        search_depth: str = "basic",
    ) -> None:
        key = api_key.get_secret_value() if isinstance(api_key, SecretStr) else api_key
        if not key or not key.strip():
            raise ConfigurationError(
                "TVLY_API_KEY is required for SEARCH_PROVIDER=tavily",
                context={"provider": self.provider.value},
            )

        self._api_key = key.strip()
        self._timeout = timeout_seconds
        self._search_depth = search_depth
        self._include_domains = [domain.strip() for domain in (include_domains or []) if domain.strip()]
        self._retry_policy = RetryPolicy(attempts=max_retries, backoff_seconds=retry_backoff_seconds)
        self._client: Any | None = None

    def describe(self) -> dict[str, Any]:
        return {
            "provider": self.provider.value,
            "search_depth": self._search_depth,
            "timeout_seconds": self._timeout,
            "include_domains": self._include_domains,
        }

    def search(self, query: str, *, max_results: int = 10) -> list[SearchResult]:
        if not query or not query.strip():
            raise SearchProviderError("Search query must not be empty", provider=self.provider.value)

        client = self._client_instance()

        def _call() -> Any:
            try:
                kwargs: dict[str, Any] = {
                    "query": query,
                    "max_results": max_results,
                    "search_depth": self._search_depth,
                    "timeout": self._timeout,
                }
                if self._include_domains:
                    kwargs["include_domains"] = self._include_domains
                return client.search(**kwargs)
            except Exception as error:
                raise classify_provider_error(
                    error,
                    provider=self.provider.value,
                    operation="search",
                    context={"query": query},
                ) from error

        try:
            payload = run_with_retry(
                _call,
                self._retry_policy,
                description=f"search.tavily[{query[:60]}]",
            )
        except ProviderError as error:
            raise SearchProviderError(str(error), context=dict(error.context), cause=error) from error

        return self._map_results(payload, query=query, limit=max_results)

    # ------------------------------------------------------------- internals
    def _client_instance(self) -> Any:
        if self._client is None:
            from tavily import TavilyClient

            self._client = TavilyClient(api_key=self._api_key)
        return self._client

    def _map_results(self, payload: Any, *, query: str, limit: int) -> list[SearchResult]:
        if not isinstance(payload, dict):
            raise ProviderResponseError(
                "Tavily returned a non-object payload",
                provider=self.provider.value,
                context={"payload_type": type(payload).__name__},
            )

        raw_results = payload.get("results") or []
        if not isinstance(raw_results, list):
            raise ProviderResponseError(
                "Tavily 'results' must be a list",
                provider=self.provider.value,
            )

        mapped: list[SearchResult] = []
        skipped = 0
        for item in raw_results[:limit]:
            if not isinstance(item, dict):
                skipped += 1
                continue
            try:
                mapped.append(
                    SearchResult(
                        title=str(item.get("title") or ""),
                        url=str(item.get("url") or ""),
                        content=str(item.get("content") or ""),
                        score=_coerce_score(item.get("score")),
                        search_query=query,
                    )
                )
            except Exception as error:  # noqa: BLE001 - one bad row must not fail the batch
                skipped += 1
                logger.debug(
                    "search_result_skipped",
                    extra={"provider": self.provider.value, "error": type(error).__name__},
                )

        if skipped:
            logger.warning(
                "search_results_partially_mapped",
                extra={"provider": self.provider.value, "skipped": skipped, "kept": len(mapped)},
            )
        return mapped


def _coerce_score(value: Any) -> float:
    """Clamp provider scores into the ``0..1`` contract enforced by the schema."""
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    return min(max(score, 0.0), 1.0)
