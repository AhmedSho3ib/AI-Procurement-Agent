"""FastAPI dependency wiring.

Dependencies only *resolve* objects that the composition root already built. They
never construct providers, run queries or contain business decisions.
"""

from __future__ import annotations

from fastapi import Request

from src.container import AppContainer
from src.controllers.procurement_ctrl import ProcurementController
from src.utils.config import Settings

__all__ = ["get_container", "get_procurement_controller", "get_settings_from_container"]


def get_container(request: Request) -> AppContainer:
    """Return the container built once at application startup."""
    container: AppContainer = request.app.state.container
    return container


def get_procurement_controller(request: Request) -> ProcurementController:
    """Return the procurement controller built at startup."""
    return get_container(request).procurement_controller


def get_settings_from_container(request: Request) -> Settings:
    """Return the resolved application settings."""
    return get_container(request).settings
