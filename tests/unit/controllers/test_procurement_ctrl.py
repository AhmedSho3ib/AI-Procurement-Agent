"""Controller behaviour: pure delegation, no business rules."""

from __future__ import annotations

from typing import Any

import pytest

from src.controllers.BaseController import BaseController
from src.controllers.procurement_ctrl import ProcurementController
from src.schemas.procurement import ProcurementRequest, ProcurementResponse, ProcurementStatus
from src.utils.config import Settings
from src.utils.exceptions import NotFoundError


class RecordingService:
    """Records calls and returns a canned response."""

    def __init__(self, response: ProcurementResponse | None = None, error: Exception | None = None) -> None:
        self.calls: list[Any] = []
        self._response = response
        self._error = error

    def run(self, request: ProcurementRequest) -> ProcurementResponse:
        self.calls.append(("run", request))
        if self._error is not None:
            raise self._error
        assert self._response is not None
        return self._response

    def get(self, request_id: str) -> ProcurementResponse:
        self.calls.append(("get", request_id))
        if self._error is not None:
            raise self._error
        assert self._response is not None
        return self._response


def _response(request: ProcurementRequest) -> ProcurementResponse:
    return ProcurementResponse(request_id="req-1", status=ProcurementStatus.COMPLETED, request=request)


class TestBaseController:
    def test_exposes_settings_to_subclasses(self, settings: Settings) -> None:
        class Concrete(BaseController):
            pass

        controller = Concrete(settings)
        assert controller.settings is settings


class TestProcurementController:
    def test_create_procurement_delegates_and_returns_the_response(
        self, procurement_request: ProcurementRequest, settings: Settings
    ) -> None:
        service = RecordingService(response=_response(procurement_request))
        controller = ProcurementController(service, settings)  # type: ignore[arg-type]

        result = controller.create_procurement(procurement_request)

        assert result.status is ProcurementStatus.COMPLETED
        assert service.calls == [("run", procurement_request)]

    def test_get_procurement_delegates_by_id(self, settings: Settings) -> None:
        request = ProcurementRequest(product="office chair", websites=["www.jumia.com.eg"])
        service = RecordingService(response=_response(request))
        controller = ProcurementController(service, settings)  # type: ignore[arg-type]

        result = controller.get_procurement("req-1")

        assert result.request_id == "req-1"
        assert service.calls == [("get", "req-1")]

    def test_service_errors_propagate_for_the_error_handler_to_translate(self, settings: Settings) -> None:
        service = RecordingService(error=NotFoundError("missing"))
        controller = ProcurementController(service, settings)  # type: ignore[arg-type]

        with pytest.raises(NotFoundError):
            controller.get_procurement("missing-id")

    def test_controller_holds_no_mutable_state_between_calls(
        self, procurement_request: ProcurementRequest, settings: Settings
    ) -> None:
        service = RecordingService(response=_response(procurement_request))
        controller = ProcurementController(service, settings)  # type: ignore[arg-type]

        controller.create_procurement(procurement_request)
        controller.create_procurement(procurement_request)

        assert len(service.calls) == 2
        assert controller.settings is settings

    def test_controller_has_no_provider_imports(self) -> None:
        # A structural guard: the controller must not reach into stores or SQL.
        import inspect

        module = inspect.getmodule(ProcurementController)
        assert module is not None
        module_source = inspect.getsource(module)
        assert "src.stores" not in module_source
        assert "sqlite3" not in module_source
        assert "crewai" not in module_source
