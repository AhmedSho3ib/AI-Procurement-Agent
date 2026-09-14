"""Deterministic LLM test double.

Purpose: make the whole procurement workflow runnable end to end with **zero
credentials and zero network calls** (unit tests, CI, `make smoke`). It is
deliberately deterministic: identical input always yields identical output.

The synthetic data produced here is sample data for development only. It is not
business logic and never runs when a real provider is configured.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

from src.schemas.products import Product, Products, ScrapedProduct
from src.schemas.reports import ProcurementReport
from src.schemas.search import SuggestedSearchQueries
from src.stores.llm.LLMEnums import LLMProvider
from src.stores.llm.LLMInterface import LLMInterface, StructuredModel
from src.utils.exceptions import ProviderResponseError

__all__ = ["MockProvider"]

#: Structured outputs this double implements, keyed by response model name.
_BUILDERS: dict[str, str] = {
    "SuggestedSearchQueries": "_build_suggested_search_queries",
    "Products": "_build_products",
    "ProcurementReport": "_build_procurement_report",
}


def _as_list(value: Any) -> list[Any]:
    """Accept a list or a comma separated string (prompt variables are strings)."""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [item for item in value if str(item).strip()]
    return [item.strip() for item in str(value).split(",") if item.strip()]


class MockProvider(LLMInterface):
    """In-process, offline implementation of :class:`LLMInterface`."""

    provider = LLMProvider.MOCK

    def __init__(self, model: str = "mock-llm", *, temperature: float = 0.0) -> None:
        self._model = model
        self._temperature = temperature

    @property
    def model_name(self) -> str:
        return self._model

    def generate(self, prompt: str, *, system_prompt: str | None = None) -> str:
        # The interface requires ``system_prompt``; the double has no system role.
        del system_prompt
        return f"[mock-llm] {prompt.strip()[:200]}"

    def generate_structured(
        self,
        prompt: str,
        response_model: type[StructuredModel],
        *,
        system_prompt: str | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> StructuredModel:
        # Instructions and system prompt are irrelevant to a deterministic double.
        del prompt, system_prompt
        builder_name = _BUILDERS.get(response_model.__name__)
        builder = getattr(self, builder_name, None) if builder_name else None
        if builder is None:
            raise ProviderResponseError(
                "MockProvider does not implement structured output for this model",
                provider=self.provider.value,
                context={"response_model": response_model.__name__, "supported": sorted(_BUILDERS)},
            )
        payload: BaseModel = builder(dict(context or {}))
        if not isinstance(payload, response_model):
            raise ProviderResponseError(
                f"MockProvider produced {type(payload).__name__} for {response_model.__name__}",
                provider=self.provider.value,
            )
        return payload

    def as_agent_llm(self) -> Any:
        """Return a CrewAI handle so crew wiring can be built without network calls.

        The handle uses a placeholder key: it can be inspected and passed to agents,
        but *invoking* it would fail. Use ``WORKFLOW=sequential`` with mock providers
        for credential-free end-to-end runs.
        """
        try:
            from crewai import LLM
        except ImportError as error:  # pragma: no cover - crewai is a runtime dependency
            from src.utils.exceptions import ConfigurationError

            raise ConfigurationError("crewai is required to build agent handles") from error
        return LLM(model="openai/gpt-4o-mini", api_key="mock-key-not-used", temperature=self._temperature)

    # --------------------------------------------------------------- builders
    def _build_suggested_search_queries(self, context: Mapping[str, Any]) -> SuggestedSearchQueries:
        raw_request = context.get("request")
        request: Mapping[str, Any] = raw_request if isinstance(raw_request, Mapping) else {}
        product = str(request.get("product") or context.get("product") or "product").strip()
        websites = _as_list(request.get("websites") or context.get("websites")) or ["example.com"]
        raw_max = request.get("max_queries") or context.get("max_queries") or len(websites) * 2 or 1
        max_queries = int(raw_max)

        queries: list[str] = []
        variants = ("best price", "specifications", "official store", "reviews")
        for index in range(max_queries):
            domain = str(websites[index % len(websites)])
            variant = variants[index % len(variants)]
            queries.append(f"{product} {variant} site:{domain}")
        return SuggestedSearchQueries(queries=queries)

    def _build_products(self, context: Mapping[str, Any]) -> Products:
        raw_products = context.get("scraped_products") or []
        scraped: list[ScrapedProduct] = []
        for item in raw_products:
            scraped.append(item if isinstance(item, ScrapedProduct) else ScrapedProduct.model_validate(item))

        # Deterministic ordering: best "value for a price strategy" = cheapest first.
        ordered = sorted(
            (item for item in scraped if item.product_current_price > 0),
            key=lambda item: (item.product_current_price, item.page_url),
        )
        total = len(ordered)
        products: list[Product] = []
        for position, item in enumerate(ordered):
            rank = max(1, 5 - (position * 5 // max(total, 1)))
            price = f"{item.product_current_price:.2f} {item.product_currency or ''}".strip()
            products.append(
                Product(
                    **item.model_dump(),
                    agent_recommendation_rank=rank,
                    agent_recommendation_notes=[
                        f"Mock evaluation: price {price}",
                        "Cheapest option in this comparison set."
                        if position == 0
                        else "Higher priced alternative.",
                    ],
                )
            )
        return Products(products=products)

    def _build_procurement_report(self, context: Mapping[str, Any]) -> ProcurementReport:
        product = str(context.get("product") or "the requested product")
        company = str(context.get("company_name") or "Rankyx")
        websites = (
            ", ".join(str(site) for site in _as_list(context.get("websites"))) or "the target marketplaces"
        )
        country = str(context.get("country") or "the target country")
        raw_products = context.get("products") or []
        products = [
            item if isinstance(item, Product) else Product.model_validate(item) for item in raw_products
        ]
        priced = [item for item in products if item.product_current_price > 0]
        cheapest = min(priced, key=lambda item: item.product_current_price, default=None)
        most_expensive = max(priced, key=lambda item: item.product_current_price, default=None)

        if cheapest is not None and most_expensive is not None:
            price_range = (
                f"Prices range from {cheapest.product_current_price:.2f} to "
                f"{most_expensive.product_current_price:.2f}."
            )
            findings = (
                f"{len(products)} products passed validation. The cheapest offer is "
                f"'{cheapest.product_title}' and the most expensive is "
                f"'{most_expensive.product_title}'."
            )
            shortlist = [
                f"Shortlist '{cheapest.product_title}' for budget-focused purchasing.",
                f"Benchmark '{most_expensive.product_title}' before approving a premium purchase.",
            ]
        else:
            price_range = "No comparable prices were extracted."
            findings = "No product passed deterministic validation."
            shortlist = ["Re-run with a wider set of marketplaces or a lower score threshold."]

        return ProcurementReport(
            title=f"Procurement Report: {product}",
            executive_summary=(
                f"{company} evaluated {len(products)} product listings for '{product}' across "
                f"{websites} in {country}. {price_range}"
            ),
            introduction=(
                f"This report supports a data-driven purchasing decision for '{product}'. "
                f"The scope covers the single-product pages discovered on {websites}."
            ),
            methodology=(
                "Search queries were generated for the requested product, executed against the "
                "configured search provider, then validated (URL structure, allowed domains, "
                "product-page heuristics), filtered by relevance score and deduplicated before "
                "product pages were scraped into a structured schema."
            ),
            findings=findings,
            analysis=(
                "Price spread indicates clear market segmentation. Higher priced listings bundle "
                "more automation, while the lowest priced listings trade features for cost "
                "efficiency."
            ),
            recommendations=[
                *shortlist,
                "Re-validate prices at purchase time; listings change frequently.",
            ],
            conclusion=(
                f"Proceeding with the recommended shortlist for '{product}' offers the best "
                "balance of price and capability."
            ),
            appendices=(
                "Raw extracted product data, the generated queries and the deterministic price "
                "statistics are attached in the machine readable sections of this report."
            ),
        )
