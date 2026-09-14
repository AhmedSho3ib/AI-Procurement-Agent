"""Procurement controller: coordinates API requests with the procurement service."""

from __future__ import annotations

from src.controllers.BaseController import BaseController
from src.schemas.procurement import ProcurementRequest, ProcurementResponse
from src.services.procurement_service import ProcurementService
from src.utils.config import Settings
from src.utils.logging import get_logger

__all__ = ["ProcurementController"]

logger = get_logger(__name__)


class ProcurementController(BaseController):
    """Stateless coordinator for procurement operations.

    It exists so that the HTTP layer never talks to the domain layer directly:
    the route validates a schema and calls this controller, which delegates to the
    service. Adding request logging, metering or a caching decorator later happens
    in one place, without touching routes or services.
    """

    def __init__(self, service: ProcurementService, settings: Settings) -> None:
        super().__init__(settings)
        self._service = service

    def create_procurement(self, request: ProcurementRequest) -> ProcurementResponse:
        """Run a procurement research workflow for ``request``."""
        return self._service.run(request)

    def get_procurement(self, request_id: str) -> ProcurementResponse:
        """Return a previously stored procurement run."""
        return self._service.get(request_id)
