"""Health endpoint integration tests (real FastAPI app, mock providers)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


class TestHealth:
    def test_returns_ok_without_contacting_providers(self, client: TestClient) -> None:
        response = client.get("/health")

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "ok"
        assert payload["workflow"] == "sequential"
        assert payload["environment"] == "test"
        assert payload["providers"]["llm"]["provider"] == "mock"

    def test_reports_observability_as_disabled_not_faked(self, client: TestClient) -> None:
        payload = client.get("/health").json()
        observability = payload["observability"]
        assert observability["enabled"] is False
        assert observability["active"] is False
        assert observability["last_error"] is None

    def test_never_exposes_credentials(self, client: TestClient) -> None:
        body = client.get("/health").text
        for marker in ("AIza", "AQ.Ab", "sk-", "tvly-", "sgai-", "api_key"):
            assert marker not in body

    def test_configuration_summary_reports_booleans_only(self, client: TestClient) -> None:
        payload = client.get("/health").json()
        assert payload["configuration"]["credentials_configured"] == {
            "google": False,
            "tavily": False,
            "scrapegraph": False,
            "agentops": False,
        }

    def test_root_endpoint_points_at_docs_and_health(self, client: TestClient) -> None:
        payload = client.get("/").json()
        assert payload["health"] == "/health"
        assert payload["docs"] == "/docs"

    def test_openapi_schema_documents_both_endpoints(self, client: TestClient) -> None:
        schema = client.get("/openapi.json").json()
        assert "/health" in schema["paths"]
        assert "/api/v1/procurement" in schema["paths"]
