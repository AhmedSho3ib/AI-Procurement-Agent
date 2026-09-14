"""Product, specification and price data contracts.

Two levels of product modelling are intentional:

``ScrapedProduct``
    What a scraping provider can actually observe on a product page. This is the
    strict schema handed to ScrapeGraph.

``Product``
    The scraped product enriched with the extraction agent's ranking and notes,
    i.e. what the procurement report is built from.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["PriceSummary", "Product", "ProductSpecification", "Products", "ScrapedProduct"]


class ProductSpecification(BaseModel):
    """A single comparable product specification (name/value pair)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    specification_name: str = Field(min_length=1, max_length=120)
    specification_value: str = Field(min_length=1, max_length=500)


class ScrapedProduct(BaseModel):
    """Raw, strictly validated product data extracted from a product page."""

    model_config = ConfigDict(str_strip_whitespace=True)

    page_url: str = Field(min_length=1, max_length=2048, description="The original product page URL")
    product_title: str = Field(default="", max_length=500, description="The product title")
    product_image_url: str | None = Field(default=None, max_length=2048, description="Main image URL")
    product_url: str = Field(default="", max_length=2048, description="Canonical product URL")
    product_current_price: float = Field(
        default=0.0,
        ge=0.0,
        description="Current price. 0 means 'not extracted' and is dropped by product filtering.",
    )
    product_original_price: float | None = Field(
        default=None, ge=0.0, description="Price before discount, if any"
    )
    product_discount_percentage: float | None = Field(
        default=None, ge=0.0, le=100.0, description="Discount percentage, if any"
    )
    product_currency: str | None = Field(
        default=None, max_length=8, description="ISO or display currency code detected on the page"
    )
    product_specs: list[ProductSpecification] = Field(
        default_factory=list,
        max_length=5,
        description="The most important specifications to compare",
    )


class Product(ScrapedProduct):
    """A scraped product enriched with the agent's recommendation ranking."""

    agent_recommendation_rank: int = Field(
        default=1,
        ge=1,
        le=5,
        description="Recommendation rank out of 5 (higher is better) for this procurement",
    )
    agent_recommendation_notes: list[str] = Field(
        default_factory=list,
        max_length=10,
        description="Why this product is or is not recommended compared to the alternatives",
    )

    @property
    def effective_url(self) -> str:
        """Canonical URL used for deduplication and reporting."""
        return self.product_url or self.page_url


class Products(BaseModel):
    """Collection wrapper used as the extraction agent's structured output."""

    products: list[Product] = Field(default_factory=list)

    def __len__(self) -> int:
        return len(self.products)


class PriceSummary(BaseModel):
    """Deterministic price aggregation for the report's findings section.

    Computed by the service (business rule) and passed to the reporting store so
    that providers only ever render data.
    """

    product_count: int = Field(default=0, ge=0)
    currency: str | None = None
    min_price: float | None = Field(default=None, ge=0.0)
    max_price: float | None = Field(default=None, ge=0.0)
    average_price: float | None = Field(default=None, ge=0.0)
    median_price: float | None = Field(default=None, ge=0.0)
    cheapest_product_title: str | None = None
    cheapest_product_url: str | None = None
    most_expensive_product_title: str | None = None
