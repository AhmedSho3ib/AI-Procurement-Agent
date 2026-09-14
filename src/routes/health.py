"""Health and readiness endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from src.container import AppContainer
from src.routes.dependencies import get_container

__all__ = ["router"]

router = APIRouter(tags=["health"])


@router.get("/health", summary="Liveness and configuration probe")
def health(container: Annotated[AppContainer, Depends(get_container)]) -> dict[str, Any]:
    """Return application status and the *non-secret* effective configuration.

    Deliberately does not call any external provider: a health check that depends
    on a third party turns their outage into your outage. Provider credentials are
    reported as booleans only.
    """
    settings = container.settings
    return {
        "status": "ok",
        "app_name": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
        "timestamp": datetime.now(UTC).isoformat(),
        "workflow": container.workflow.name,
        "providers": container.provider_info,
        "observability": container.observability.describe(),
        "configuration": settings.safe_summary(),
    }


@router.get("/", include_in_schema=False)
def root() -> dict[str, str]:
    """Minimal landing response pointing at the interactive documentation."""
    return {"service": "ai-procurement-agent", "docs": "/docs", "health": "/health"}
