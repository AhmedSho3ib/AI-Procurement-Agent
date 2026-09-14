"""Deterministic, offline scraping provider used for tests, CI and local demos."""

from __future__ import annotations

import hashlib

from src.schemas.products import ProductSpecification, ScrapedProduct
from src.stores.scraping.ScraperEnums import ScraperProvider
from src.stores.scraping.ScraperInterface import ScraperInterface
from src.utils.exceptions import ScrapingError

__all__ = ["MockProvider"]

_CURRENCY = "EGP"
_SPEC_CATALOGUE: tuple[tuple[str, str], ...] = (
    ("Type", "Countertop"),
    ("Power", "1000 W"),
    ("Capacity", "12 cups"),
    ("Warranty", "1 year"),
)
_BRANDS = ("Northwind", "Acme", "Contoso", "Globex", "Initech")


class MockProvider(ScraperInterface):
    """Derives a stable synthetic product from a URL.

    The same URL always yields the same product and price, so the mock pipeline
    is reproducible across test runs and CI machines.
    """

    provider = ScraperProvider.MOCK

    def __init__(self, *, base_price: float = 1200.0, price_step: float = 350.0) -> None:
        self._base_price = base_price
        self._price_step = price_step

    def extract_product(self, url: str) -> ScrapedProduct:
        normalised = (url or "").strip()
        if not normalised:
            raise ScrapingError("A page URL is required for extraction", provider=self.provider.value)

        digest = hashlib.sha256(normalised.encode("utf-8")).hexdigest()
        brand = _BRANDS[int(digest[:8], 16) % len(_BRANDS)]
        # A wide, prime bucket spread keeps synthetic prices realistically varied
        # while staying perfectly reproducible for a given URL.
        price = self._base_price + ((int(digest[8:16], 16) % 97) * self._price_step)
        discount = 15.0 if int(digest[16:18], 16) % 2 == 0 else None
        original_price = round(price / (1 - (discount / 100.0)), 2) if discount else None
        slug_title = _title_from_url(normalised)

        return ScrapedProduct(
            page_url=normalised,
            product_title=f"{brand} {slug_title}",
            product_image_url=f"https://cdn.example.com/{digest[:12]}.jpg",
            product_url=normalised,
            product_current_price=round(price, 2),
            product_original_price=original_price,
            product_discount_percentage=discount,
            product_currency=_CURRENCY,
            product_specs=[
                ProductSpecification(specification_name=name, specification_value=value)
                for name, value in _SPEC_CATALOGUE[:3]
            ],
        )


def _title_from_url(url: str) -> str:
    tail = url.rstrip("/").split("/")[-1].split("?")[0]
    cleaned = "".join(char if char.isalnum() else " " for char in tail)
    words = [
        word.capitalize()
        for word in cleaned.split()
        if not word.isdigit() and word.lower() not in {"html", "htm", "php", "aspx"}
    ]
    if "Site" in words:
        # The mock search provider appends "site:<domain>" to the slug; drop it so
        # demo titles read like real products.
        words = words[: words.index("Site")]
    return " ".join(words[:8]) or "Synthetic Product"
