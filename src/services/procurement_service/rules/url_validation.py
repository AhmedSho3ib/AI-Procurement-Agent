"""Deterministic URL validation.

The LLM proposes URLs; this module decides whether they are usable. Keeping the
decision in code makes it explainable, testable and impossible for a model to
talk its way around.

Rules implemented:

* structural validity (absolute ``http(s)`` URL with a host),
* blocked schemes (``javascript:``, ``mailto:``, ``data:``, ...),
* suspicious paths (login, cart, checkout, redirects, tracking pixels),
* obvious non-product pages (blog, news, help, category, search listings),
* positive product-page signals (``/dp/``, ``/product/``, ``/p/``, ``.html``),
* optional domain allow-list derived from the request's target websites,
* URL normalisation used for deduplication and reporting.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import ParseResult, parse_qsl, urlencode, urlparse, urlunparse

__all__ = [
    "BLOCKED_SCHEMES",
    "UrlValidationResult",
    "extract_domain",
    "extract_product_identifier",
    "is_allowed_domain",
    "is_likely_product_page",
    "is_suspicious_url",
    "is_valid_url",
    "normalize_domain",
    "normalize_url",
    "validate_url",
]

BLOCKED_SCHEMES: frozenset[str] = frozenset({"javascript", "mailto", "tel", "data", "ftp", "file", "about"})

#: A narrow subset of non-product markers used for the "suspicious" verdict.
_SUSPICIOUS_PATHS: tuple[str, ...] = (
    "/login",
    "/signin",
    "/sign-in",
    "/cart",
    "/checkout",
    "/wishlist",
    "/redirect",
    "/account",
    "/compare",
)

#: Path fragments that never point at a purchasable single product page.
NON_PRODUCT_MARKERS: tuple[str, ...] = (
    "/blog",
    "/news",
    "/article",
    "/press",
    "/help",
    "/support",
    "/faq",
    "/about",
    "/contact",
    "/policy",
    "/policies",
    "/terms",
    "/privacy",
    "/careers",
    "/category",
    "/categories",
    "/collections",
    "/browse",
    "/catalog",
    "/store-locator",
    "/sitemap",
    "/login",
    "/signin",
    "/sign-in",
    "/register",
    "/cart",
    "/checkout",
    "/account",
    "/orders",
    "/wishlist",
    "/compare",
    "/track",
    "/redirect",
)

#: Path fragments that usually *do* point at a single product page.
PRODUCT_MARKERS: tuple[str, ...] = (
    "/dp/",
    "/gp/product/",
    "/product/",
    "/products/",
    "/item/",
    "/items/",
    "/itm/",
    "/offer/",
    "/p/",
    "/prd/",
    "/pd/",
    ".html",
    ".htm",
)

#: Query parameters that only exist for tracking; removed when normalising.
_TRACKING_PARAMS: frozenset[str] = frozenset(
    {
        "gclid",
        "fbclid",
        "msclkid",
        "ref",
        "ref_",
        "referrer",
        "tag",
        "affid",
        "aff_id",
        "spm",
        "th",
        "psc",
        "pd_rd_r",
        "pd_rd_w",
        "pd_rd_wg",
        "pf_rd_p",
        "pf_rd_r",
        "_encoding",
        "keywords",
    }
)

#: Domains that are never product sources.
_SUSPICIOUS_DOMAIN_MARKERS: tuple[str, ...] = (
    "bit.ly",
    "tinyurl.com",
    "t.co",
    "goo.gl",
    "doubleclick.net",
    "adservice.google",
    "s.click",
)

_MAX_URL_LENGTH = 2048


@dataclass(frozen=True, slots=True)
class UrlValidationResult:
    """Outcome of validating a single URL."""

    url: str
    normalized_url: str
    domain: str
    is_valid: bool
    is_suspicious: bool
    is_product_page: bool
    is_allowed_domain: bool
    reasons: list[str] = field(default_factory=list)

    @property
    def is_usable(self) -> bool:
        """The single question the pipeline asks: can this URL be scraped?"""
        return self.is_valid and not self.is_suspicious and self.is_product_page and self.is_allowed_domain

    def describe(self) -> str:
        return "; ".join(self.reasons) if self.reasons else "valid product page"


def is_valid_url(url: str) -> bool:
    """Structural validity: absolute http(s) URL with a host and no whitespace."""
    return _parse(url) is not None


def normalize_domain(url: str) -> str:
    """Return the lowercase host without a leading ``www.`` (``""`` when absent)."""
    parsed = _parse(url)
    if parsed is None:
        return ""
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def extract_domain(website: str) -> str | None:
    """Extract a comparable domain from a request website entry.

    Handles ``"www.amazon.eg"``, ``"https://www.noon.com/egypt-en"`` and
    ``"Jumia Egypt"`` (the last one returns ``None``: a human readable label
    cannot be matched against a URL host, so the allow-list simply ignores it).
    """
    candidate = (website or "").strip().lower()
    if not candidate:
        return None
    if " " in candidate and "://" not in candidate:
        return None
    if "://" not in candidate:
        candidate = f"https://{candidate}"
    domain = normalize_domain(candidate)
    if not domain or "." not in domain:
        return None
    return domain


def is_allowed_domain(url: str, allowed_domains: frozenset[str] | set[str] | None) -> bool:
    """Allow when the URL host equals or is a subdomain of an allowed domain.

    An empty/``None`` allow-list means "no domain restriction" (for example when
    the caller supplied marketplace display names instead of domains).
    """
    if not allowed_domains:
        return True
    domain = normalize_domain(url)
    if not domain:
        return False
    return any(domain == allowed or domain.endswith(f".{allowed}") for allowed in allowed_domains)


def is_suspicious_url(url: str) -> bool:
    """Detect redirects, trackers, ad networks and obviously non-product links."""
    parsed = _parse(url)
    if parsed is None:
        return True

    scheme = (parsed.scheme or "").lower()
    if scheme in BLOCKED_SCHEMES:
        return True

    domain = normalize_domain(url)
    if any(marker in domain for marker in _SUSPICIOUS_DOMAIN_MARKERS):
        return True

    if len(url) > _MAX_URL_LENGTH:
        return True

    path = (parsed.path or "").lower()
    query = (parsed.query or "").lower()
    if "redirect" in query or "url=http" in query:
        return True
    return any(marker in path for marker in _SUSPICIOUS_PATHS)


def is_likely_product_page(url: str) -> bool:
    """Heuristic: does this URL look like a single product detail page?"""
    parsed = _parse(url)
    if parsed is None:
        return False

    path = (parsed.path or "").lower()
    if not path or path == "/":
        return False
    if any(marker in path for marker in NON_PRODUCT_MARKERS):
        return False
    if _has_product_identifier(parsed):
        return True
    return any(marker in path for marker in PRODUCT_MARKERS)


_PRODUCT_ID_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"/dp/(?P<id>[A-Z0-9]{8,14})", re.IGNORECASE),  # amazon ASIN
    re.compile(r"/(?P<id>N[0-9A-Z]{6,12})/p/?", re.IGNORECASE),  # noon product code
    re.compile(r"[-/](?P<id>\d{5,})\.html", re.IGNORECASE),  # jumia style numeric id
)


def _has_product_identifier(parsed: ParseResult) -> bool:
    return any(pattern.search(parsed.path or "") for pattern in _PRODUCT_ID_PATTERNS)


def extract_product_identifier(url: str) -> str | None:
    """Return a marketplace-native product identifier when one is recognisable."""
    parsed = _parse(url)
    if parsed is None:
        return None
    path = parsed.path or ""
    for pattern in _PRODUCT_ID_PATTERNS:
        match = pattern.search(path)
        if match:
            return match.group("id").upper()
    return None


def normalize_url(url: str, *, drop_query: bool = False) -> str:
    """Canonical form used for deduplication and reporting.

    Lowercases the scheme/host, removes ``www.``, tracking parameters and the
    fragment, sorts the remaining query parameters and drops a trailing slash.
    """
    parsed = _parse(url)
    if parsed is None:
        return (url or "").strip()

    netloc = (parsed.hostname or "").lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"

    path = parsed.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    query = ""
    if not drop_query and parsed.query:
        kept = [
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=False)
            if key.lower() not in _TRACKING_PARAMS and not key.lower().startswith("utm_")
        ]
        query = urlencode(sorted(kept))

    return urlunparse((parsed.scheme.lower(), netloc, path, "", query, ""))


def validate_url(
    url: str,
    *,
    allowed_domains: frozenset[str] | set[str] | None = None,
    require_product_page: bool = True,
) -> UrlValidationResult:
    """Validate ``url`` and explain the verdict in ``reasons``."""
    raw = (url or "").strip()
    parsed = _parse(raw)
    domain = normalize_domain(raw)
    normalized = normalize_url(raw) if parsed is not None else raw
    reasons: list[str] = []

    if parsed is None:
        return UrlValidationResult(
            url=raw,
            normalized_url=raw,
            domain=domain,
            is_valid=False,
            is_suspicious=False,
            is_product_page=False,
            is_allowed_domain=False,
            reasons=["not a valid absolute http(s) URL"],
        )

    if (parsed.scheme or "").lower() in BLOCKED_SCHEMES:
        reasons.append(f"blocked scheme '{parsed.scheme}'")

    suspicious = is_suspicious_url(raw)
    if suspicious:
        reasons.append("matches suspicious URL patterns (login/cart/tracker/redirect)")

    product_page = is_likely_product_page(raw)
    if require_product_page and not product_page:
        reasons.append("does not look like a single product page")

    allowed = is_allowed_domain(raw, allowed_domains)
    if not allowed:
        reasons.append(f"domain '{domain}' is outside the requested marketplaces")

    return UrlValidationResult(
        url=raw,
        normalized_url=normalized,
        domain=domain,
        is_valid=True,
        is_suspicious=suspicious,
        is_product_page=product_page,
        is_allowed_domain=allowed,
        reasons=reasons,
    )


def _parse(url: str) -> ParseResult | None:
    candidate = (url or "").strip()
    if not candidate or any(char.isspace() for char in candidate):
        return None
    try:
        parsed = urlparse(candidate)
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"}:
        return None
    if not parsed.netloc:
        return None
    return parsed
