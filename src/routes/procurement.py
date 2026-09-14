"""Procurement API endpoints.

The route layer does exactly four things: declare the HTTP contract, validate the
request schema, delegate to the controller, and return the response. Any logic
beyond that belongs further down the stack.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, status

from src.controllers.procurement_ctrl import ProcurementController
from src.routes.dependencies import get_procurement_controller
from src.schemas.procurement import ProcurementRequest, ProcurementResponse

__all__ = ["router"]

router = APIRouter(prefix="/procurement", tags=["procurement"])

Controller = Annotated[ProcurementController, Depends(get_procurement_controller)]


@router.post(
    "",
    response_model=ProcurementResponse,
    status_code=status.HTTP_200_OK,
    summary="Run a procurement research workflow",
    responses={
        422: {"description": "Request validation failed"},
        429: {"description": "A provider throttled the request"},
        502: {"description": "The LLM, search or scraping provider failed"},
        503: {"description": "A provider is temporarily unavailable"},
        504: {"description": "A provider timed out"},
    },
)
def create_procurement(
    request: ProcurementRequest,
    controller: Controller,
) -> ProcurementResponse:
    """Generate queries, search, extract products and produce a procurement report."""
    return controller.create_procurement(request)


@router.get(
    "/{request_id}",
    response_model=ProcurementResponse,
    summary="Retrieve a previous procurement run",
    responses={404: {"description": "No stored run for this id"}},
)
def get_procurement(
    controller: Controller,
    request_id: Annotated[str, Path(min_length=8, max_length=64, description="Run identifier")],
) -> ProcurementResponse:
    """Return a persisted procurement run (audit trail / report retrieval)."""
    return controller.get_procurement(request_id)
