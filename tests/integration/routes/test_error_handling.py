"""API error translation: typed application errors become stable HTTP contracts."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.main import create_app
from src.schemas.procurement import ProcurementRequest, ProcurementResponse
from src.utils.config import Settings
from src.utils.exceptions import (
    ProviderAuthError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    QueryGenerationError,
    SearchProviderError,
)
from tests.conftest import make_settings

pytestmark = pytest.mark.integration

PAYLOAD = {"product": "coffee machine for the office", "websites": ["www.amazon.eg"]}


class ExplodingService:
    def __init__(self, error: BaseException) -> None:
        self._error = error

    def run(self, request: ProcurementRequest) -> ProcurementResponse:
        raise self._error

    def get(self, request_id: str) -> ProcurementResponse:
        raise self._error


def _client_with_error(error: BaseException, settings: Settings | None = None) -> TestClient:
    app = create_app(settings or make_settings())
    app.state.container.procurement_controller._service = ExplodingService(error)
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_code"),
    [
        (ProviderAuthError("bad credentials", provider="gemini"), 502, "provider_auth_error"),
        (ProviderRateLimitError("slow down", provider="tavily"), 429, "provider_rate_limited"),
        (ProviderTimeoutError("too slow", provider="scrapegraph"), 504, "provider_timeout"),
        (SearchProviderError("search exploded", provider="tavily"), 502, "search_provider_failed"),
        (QueryGenerationError("no queries"), 502, "query_generation_failed"),
    ],
)
def test_application_errors_map_to_http_status_codes(
    error: BaseException, expected_status: int, expected_code: str
) -> None:
    with _client_with_error(error) as client:
        response = client.post("/api/v1/procurement", json=PAYLOAD)

    assert response.status_code == expected_status
    body = response.json()["error"]
    assert body["code"] == expected_code
    assert body["message"]
    assert body["request_id"]


def test_unexpected_errors_do_not_leak_internals() -> None:
    with _client_with_error(RuntimeError("internal detail: secret-token-xyz")) as client:
        response = client.post("/api/v1/procurement", json=PAYLOAD)

    assert response.status_code == 500
    body = response.json()["error"]
    assert body["code"] == "internal_error"
    assert "secret-token-xyz" not in response.text
    assert "RuntimeError" not in response.text


def test_error_details_are_included_for_operators() -> None:
    with _client_with_error(ProviderAuthError("bad credentials", provider="gemini")) as client:
        body = client.post("/api/v1/procurement", json=PAYLOAD).json()["error"]

    assert body["details"]["provider"] == "gemini"


def test_error_responses_are_json_even_for_unhandled_exceptions() -> None:
    with _client_with_error(ValueError("boom")) as client:
        response = client.post("/api/v1/procurement", json=PAYLOAD)

    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["error"]["code"] == "internal_error"


def test_cors_is_disabled_by_default() -> None:
    with TestClient(create_app(make_settings())) as client:
        response = client.get("/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in {key.lower() for key in response.headers}


def test_cors_is_enabled_when_configured() -> None:
    settings = make_settings(cors_allow_origins="https://app.example.com")
    with TestClient(create_app(settings)) as client:
        response = client.get("/health", headers={"Origin": "https://app.example.com"})
    assert response.headers.get("access-control-allow-origin") == "https://app.example.com"


def test_lifespan_closes_resources_cleanly() -> None:
    container = None
    with TestClient(create_app(make_settings())) as client:
        assert client.get("/health").status_code == 200
        container = client.app.state.container  # type: ignore[attr-defined]
    # After the context manager exits, the container has been shut down.
    assert container is not None
    assert container.observability.active is False


def test_container_is_available_on_every_request() -> None:
    with TestClient(create_app(make_settings())) as client:
        first = client.get("/health").json()
        second = client.get("/health").json()
    assert first["app_name"] == second["app_name"]


def test_service_is_not_recreated_between_requests() -> None:
    app = create_app(make_settings())
    with TestClient(app) as client:
        client.get("/health")
        service = app.state.container.procurement_service
        client.get("/health")
        assert app.state.container.procurement_service is service
