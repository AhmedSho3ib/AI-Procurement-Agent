"""Procurement API integration tests: the full stack with mock providers.

These tests exercise routes -> schema -> controller -> service -> workflow ->
stores -> persistence -> report rendering, with no network access and no
credentials.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration

PAYLOAD = {
    "product": "coffee machine for the office",
    "websites": ["www.amazon.eg", "www.jumia.com.eg", "www.noon.com/egypt-en"],
    "country": "Egypt",
    "language": "English",
    "max_queries": 4,
    "score_threshold": 0.10,
    "top_n": 4,
}


class TestCreateProcurement:
    def test_returns_a_complete_response(self, client: TestClient) -> None:
        response = client.post("/api/v1/procurement", json=PAYLOAD)

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "completed"
        assert payload["request"]["product"] == PAYLOAD["product"]
        assert 1 <= len(payload["queries"]) <= 4
        assert payload["search_results"]
        assert 1 <= len(payload["products"]) <= 4
        assert payload["price_summary"]["product_count"] == len(payload["products"])
        assert payload["report"]["format"] == "html"
        assert payload["duration_ms"] >= 0
        assert payload["metadata"]["workflow"] == "sequential"
        assert payload["metadata"]["providers"]["search"]["provider"] == "mock"

    def test_products_are_ranked_and_fully_populated(self, client: TestClient) -> None:
        payload = client.post("/api/v1/procurement", json=PAYLOAD).json()

        ranks = [product["agent_recommendation_rank"] for product in payload["products"]]
        assert ranks == sorted(ranks, reverse=True)
        for product in payload["products"]:
            assert product["product_current_price"] > 0
            assert product["product_title"]
            assert product["product_specs"]
            assert product["agent_recommendation_notes"]

    def test_report_contains_the_expected_sections(self, client: TestClient) -> None:
        report = client.post("/api/v1/procurement", json=PAYLOAD).json()["report"]["content"]
        for heading in (
            "Executive Summary",
            "Introduction",
            "Methodology",
            "Findings",
            "Analysis",
            "Recommendations",
            "Conclusion",
            "Appendices",
        ):
            assert heading in report

    def test_markdown_format_is_supported(self, client: TestClient) -> None:
        payload = client.post("/api/v1/procurement", json={**PAYLOAD, "report_format": "markdown"}).json()
        assert payload["report"]["format"] == "markdown"
        assert payload["report"]["content"].startswith("# ")

    def test_json_format_is_supported(self, client: TestClient) -> None:
        payload = client.post("/api/v1/procurement", json={**PAYLOAD, "report_format": "json"}).json()
        assert payload["report"]["format"] == "json"

    def test_report_can_be_skipped(self, client: TestClient) -> None:
        payload = client.post("/api/v1/procurement", json={**PAYLOAD, "generate_report": False}).json()
        assert payload["report"] is None
        assert payload["status"] == "completed"

    def test_runs_are_reproducible_with_mock_providers(self, client: TestClient) -> None:
        first = client.post("/api/v1/procurement", json=PAYLOAD).json()
        second = client.post("/api/v1/procurement", json=PAYLOAD).json()

        assert first["queries"] == second["queries"]
        assert [product["product_url"] for product in first["products"]] == [
            product["product_url"] for product in second["products"]
        ]
        assert first["request_id"] != second["request_id"]

    def test_websites_are_normalised(self, client: TestClient) -> None:
        payload = client.post(
            "/api/v1/procurement",
            json={**PAYLOAD, "websites": ["  WWW.Amazon.EG  ", "www.amazon.eg"]},
        ).json()
        assert payload["request"]["websites"] == ["www.amazon.eg"]

    def test_request_id_header_is_returned(self, client: TestClient) -> None:
        response = client.post("/api/v1/procurement", json=PAYLOAD)
        assert response.headers["x-request-id"]
        assert float(response.headers["x-process-time-ms"]) >= 0

    def test_client_supplied_request_id_is_echoed(self, client: TestClient) -> None:
        response = client.post("/api/v1/procurement", json=PAYLOAD, headers={"x-request-id": "trace-abc"})
        assert response.headers["x-request-id"] == "trace-abc"


class TestRetrieveProcurement:
    def test_stored_run_can_be_fetched_by_id(self, client: TestClient) -> None:
        created = client.post("/api/v1/procurement", json=PAYLOAD).json()

        fetched = client.get(f"/api/v1/procurement/{created['request_id']}")

        assert fetched.status_code == 200
        assert fetched.json()["request_id"] == created["request_id"]
        assert fetched.json()["report"]["content"] == created["report"]["content"]

    def test_unknown_id_returns_a_structured_404(self, client: TestClient) -> None:
        response = client.get("/api/v1/procurement/00000000-0000-0000-0000-000000000000")

        assert response.status_code == 404
        error = response.json()["error"]
        assert error["code"] == "not_found"
        assert error["request_id"]


class TestRequestValidation:
    @pytest.mark.parametrize(
        "payload",
        [
            {"websites": ["www.amazon.eg"]},  # product missing
            {"product": "x", "websites": ["www.amazon.eg"]},  # product too short
            {"product": "coffee machine", "websites": []},  # no websites
            {"product": "coffee machine", "websites": ["a.com"], "score_threshold": 5},
            {"product": "coffee machine", "websites": ["a.com"], "max_queries": 0},
            {"product": "coffee machine", "websites": ["a.com"], "top_n": 999},
            {"product": "coffee machine", "websites": ["a.com"], "report_format": "pdf"},
        ],
    )
    def test_invalid_payloads_return_422(self, client: TestClient, payload: dict[str, Any]) -> None:
        response = client.post("/api/v1/procurement", json=payload)
        assert response.status_code == 422
        assert "detail" in response.json()

    def test_error_body_never_echoes_secrets_or_internals(self, client: TestClient) -> None:
        response = client.post("/api/v1/procurement", json={"product": "x"})
        assert "Traceback" not in response.text
        assert "site-packages" not in response.text
