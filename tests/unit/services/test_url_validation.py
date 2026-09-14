"""URL validation rules.

These are the guarantees the pipeline depends on: no matter what an LLM returns,
only credible single-product URLs on the requested marketplaces get scraped.
"""

from __future__ import annotations

import pytest

from src.services.procurement_service.rules import url_validation as rules


class TestStructuralValidation:
    @pytest.mark.parametrize(
        "url",
        [
            "https://www.amazon.eg/DeLonghi-Magnifica/dp/B00400OMU0",
            "http://example.com/product/1",
            "https://www.jumia.com.eg/tornado-coffee-maker-156789.html",
        ],
    )
    def test_accepts_absolute_http_urls(self, url: str) -> None:
        assert rules.is_valid_url(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "",
            "   ",
            "not a url",
            "amazon.eg/dp/B00400OMU0",
            "javascript:alert(1)",
            "mailto:procurement@example.com",
            "ftp://example.com/file.txt",
            "https://",
        ],
    )
    def test_rejects_non_http_and_relative_urls(self, url: str) -> None:
        assert rules.is_valid_url(url) is False


class TestSuspiciousUrls:
    @pytest.mark.parametrize(
        "url",
        [
            "https://www.amazon.eg/login",
            "https://www.amazon.eg/gp/cart/view.html",
            "https://www.amazon.eg/checkout/entry",
            "https://bit.ly/3xYzAbc",
            "https://example.com/redirect?url=http://evil.test",
            "https://tracking.doubleclick.net/click?id=1",
        ],
    )
    def test_flags_suspicious_urls(self, url: str) -> None:
        assert rules.is_suspicious_url(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.amazon.eg/DeLonghi-Magnifica/dp/B00400OMU0",
            "https://www.noon.com/egypt-en/vertuo-next-coffee-machine/N40636566A/p/",
        ],
    )
    def test_allows_genuine_product_urls(self, url: str) -> None:
        assert rules.is_suspicious_url(url) is False


class TestProductPageHeuristics:
    @pytest.mark.parametrize(
        "url",
        [
            "https://www.amazon.eg/Philips-Series-2200/dp/B082Y45678",
            "https://www.noon.com/egypt-en/12-cup-coffee-maker/N12345678A/p/",
            "https://www.jumia.com.eg/kenwood-drip-coffee-maker-cm200.html",
            "https://www.amazon.eg/gp/product/B098765432",
        ],
    )
    def test_recognises_product_pages(self, url: str) -> None:
        assert rules.is_likely_product_page(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.amazon.eg/",
            "https://www.jumia.com.eg/blog/best-coffee-makers",
            "https://www.noon.com/egypt-en/category/kitchen-appliances",
            "https://www.jumia.com.eg/help/returns",
            "https://www.amazon.eg/sitemap.xml",
        ],
    )
    def test_rejects_non_product_pages(self, url: str) -> None:
        assert rules.is_likely_product_page(url) is False


class TestDomainAllowList:
    def test_empty_allow_list_means_no_restriction(self) -> None:
        assert rules.is_allowed_domain("https://anything.test/p/1", None) is True
        assert rules.is_allowed_domain("https://anything.test/p/1", frozenset()) is True

    def test_matches_exact_domain_and_subdomains(self) -> None:
        allowed = frozenset({"amazon.eg"})
        assert rules.is_allowed_domain("https://www.amazon.eg/dp/X", allowed) is True
        assert rules.is_allowed_domain("https://smile.amazon.eg/dp/X", allowed) is True
        assert rules.is_allowed_domain("https://www.amazon.com/dp/X", allowed) is False

    def test_does_not_match_lookalike_suffix(self) -> None:
        allowed = frozenset({"amazon.eg"})
        assert rules.is_allowed_domain("https://notamazon.eg/dp/X", allowed) is False

    @pytest.mark.parametrize(
        ("website", "expected"),
        [
            ("www.amazon.eg", "amazon.eg"),
            ("https://www.noon.com/egypt-en", "noon.com"),
            ("https://www.jumia.com.eg/", "jumia.com.eg"),
            ("Amazon Egypt", None),
            ("", None),
            ("localhost", None),
        ],
    )
    def test_extract_domain_handles_request_website_formats(self, website: str, expected: str | None) -> None:
        assert rules.extract_domain(website) == expected


class TestNormalization:
    def test_strips_tracking_parameters_and_fragment(self) -> None:
        normalized = rules.normalize_url(
            "https://WWW.Amazon.eg/DeLonghi/dp/B00400OMU0?utm_source=x&tag=y&th=1#reviews"
        )
        assert normalized == "https://amazon.eg/DeLonghi/dp/B00400OMU0"

    def test_sorts_remaining_query_parameters(self) -> None:
        normalized = rules.normalize_url("https://example.com/p?b=2&a=1")
        assert normalized == "https://example.com/p?a=1&b=2"

    def test_removes_trailing_slash(self) -> None:
        assert rules.normalize_url("https://example.com/product/") == "https://example.com/product"

    def test_handles_invalid_input_without_raising(self) -> None:
        assert rules.normalize_url("nonsense value") == "nonsense value"


class TestProductIdentifiers:
    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("https://www.amazon.eg/DeLonghi/dp/B00400OMU0", "B00400OMU0"),
            ("https://www.noon.com/egypt-en/vertuo/N40636566A/p/", "N40636566A"),
            ("https://www.jumia.com.eg/tornado-coffee-maker-156789.html", "156789"),
            ("https://example.com/product/name", None),
        ],
    )
    def test_extracts_marketplace_identifiers(self, url: str, expected: str | None) -> None:
        assert rules.extract_product_identifier(url) == expected


class TestValidateUrl:
    def test_reports_a_reason_for_every_rejection(self) -> None:
        verdict = rules.validate_url(
            "https://www.jumia.com.eg/blog/list", allowed_domains=frozenset({"amazon.eg"})
        )
        assert verdict.is_valid is True
        assert verdict.is_usable is False
        reasons = " | ".join(verdict.reasons)
        assert "product page" in reasons
        assert "marketplaces" in reasons

    def test_accepts_a_usable_product_url(self) -> None:
        verdict = rules.validate_url(
            "https://www.amazon.eg/DeLonghi-Magnifica/dp/B00400OMU0",
            allowed_domains=frozenset({"amazon.eg"}),
        )
        assert verdict.is_usable is True
        assert verdict.describe() == "valid product page"

    def test_invalid_url_has_a_single_clear_reason(self) -> None:
        verdict = rules.validate_url("not-a-url")
        assert verdict.is_valid is False
        assert verdict.reasons == ["not a valid absolute http(s) URL"]
