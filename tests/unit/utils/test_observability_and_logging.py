"""Observability and logging behaviour.

The AgentOps integration is optional and fail-safe. These tests prove three
properties that matter in production:

* telemetry is never enabled implicitly,
* a telemetry failure never propagates and never pretends to be working,
* when enabled, the session is wired and closed correctly.
"""

from __future__ import annotations

import json
import logging
import sys
import types
from typing import Any

import pytest
from pydantic import SecretStr

from src.utils.config import Settings
from src.utils.logging import configure_logging, get_logger
from src.utils.observability import ObservabilityManager
from tests.conftest import make_settings

VALID_KEY = "7f2f0a1e-1111-4222-8333-444455556666"


class FakeAgentOps(types.ModuleType):
    """Stands in for the ``agentops`` module."""

    def __init__(self, *, fail: bool = False) -> None:
        super().__init__("agentops")
        self.init_calls: list[dict[str, Any]] = []
        self.end_calls: list[Any] = []
        self._fail = fail

    def init(self, **kwargs: Any) -> None:
        self.init_calls.append(kwargs)
        if self._fail:
            raise RuntimeError("agentops init exploded")

    def end_session(self, status: Any = None) -> None:
        self.end_calls.append(status)


@pytest.fixture
def fake_agentops(monkeypatch: pytest.MonkeyPatch) -> FakeAgentOps:
    module = FakeAgentOps()
    monkeypatch.setitem(sys.modules, "agentops", module)
    return module


class TestDisabledByDefault:
    def test_is_not_started_and_reports_honestly(self, settings: Settings) -> None:
        manager = ObservabilityManager(settings)

        assert manager.enabled is False
        assert manager.start() is False
        assert manager.active is False
        assert manager.describe() == {
            "provider": "agentops",
            "enabled": False,
            "active": False,
            "last_error": None,
        }

    def test_disabled_manager_never_imports_agentops(self, settings: Settings) -> None:
        sys.modules.pop("agentops", None)
        ObservabilityManager(settings).start()
        assert "agentops" not in sys.modules

    def test_end_without_start_is_a_no_op(self, settings: Settings) -> None:
        ObservabilityManager(settings).end("Success")


class TestMisconfiguration:
    def test_enabled_without_a_key_reports_the_cause(self) -> None:
        # Settings normally rejects this combination; the manager defends against it
        # anyway, which is what these two tests exercise.
        settings = make_settings(agentops_enabled=False).model_copy(
            update={"agentops_enabled": True, "agentops_api_key": None}
        )
        manager = ObservabilityManager(settings)

        assert manager.start() is False
        assert manager.active is False
        assert manager.last_error is not None
        assert "AGENTOPS_API_KEY" in manager.last_error

    def test_blank_key_is_treated_as_missing(self) -> None:
        settings = make_settings(agentops_enabled=False).model_copy(
            update={"agentops_enabled": True, "agentops_api_key": SecretStr("   ")}
        )
        assert ObservabilityManager(settings).start() is False


class TestEnabled:
    def test_starts_the_session_with_the_configured_settings(self, fake_agentops: FakeAgentOps) -> None:
        settings = make_settings(
            agentops_enabled=True,
            agentops_api_key=VALID_KEY,
            agentops_default_tags="crewai,procurement",
            agentops_skip_auto_end_session=True,
        )
        manager = ObservabilityManager(settings)

        assert manager.start() is True
        assert manager.active is True
        assert manager.describe()["active"] is True

        call = fake_agentops.init_calls[0]
        assert call["api_key"] == VALID_KEY
        assert call["fail_safe"] is True
        assert call["skip_auto_end_session"] is True
        assert call["default_tags"] == ["crewai", "procurement"]

    def test_closes_the_session_on_shutdown(self, fake_agentops: FakeAgentOps) -> None:
        settings = make_settings(agentops_enabled=True, agentops_api_key=VALID_KEY)
        manager = ObservabilityManager(settings)
        manager.start()

        manager.end("Success")

        assert fake_agentops.end_calls == ["Success"]
        assert manager.active is False

    def test_non_uuid_key_still_attempts_and_does_not_fake_success(self, fake_agentops: FakeAgentOps) -> None:
        settings = make_settings(agentops_enabled=True, agentops_api_key="not-a-uuid-key")
        manager = ObservabilityManager(settings)

        # The shape is suspicious but the SDK remains the source of truth.
        assert manager.start() is True
        assert fake_agentops.init_calls[0]["api_key"] == "not-a-uuid-key"

    def test_init_failure_is_swallowed_and_reported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        module = FakeAgentOps(fail=True)
        monkeypatch.setitem(sys.modules, "agentops", module)
        settings = make_settings(agentops_enabled=True, agentops_api_key=VALID_KEY)
        manager = ObservabilityManager(settings)

        assert manager.start() is False
        assert manager.active is False
        assert manager.last_error is not None
        assert "RuntimeError" in manager.last_error

    def test_end_session_failure_never_propagates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        module = FakeAgentOps()

        def exploding_end(status: Any = None) -> None:
            raise RuntimeError("dashboard unavailable")

        module.end_session = exploding_end  # type: ignore[method-assign]
        monkeypatch.setitem(sys.modules, "agentops", module)
        settings = make_settings(agentops_enabled=True, agentops_api_key=VALID_KEY)
        manager = ObservabilityManager(settings)
        manager.start()

        manager.end("Success")  # must not raise

        assert manager.active is False
        assert manager.last_error is not None

    def test_health_describe_never_includes_the_credential(self, fake_agentops: FakeAgentOps) -> None:
        settings = make_settings(agentops_enabled=True, agentops_api_key=VALID_KEY)
        manager = ObservabilityManager(settings)
        manager.start()

        assert VALID_KEY not in json.dumps(manager.describe())


class TestTracingEnvironment:
    def test_disables_crewai_exporter_when_agentops_owns_the_session(
        self, fake_agentops: FakeAgentOps, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CREWAI_TRACING_ENABLED", "true")
        settings = make_settings(agentops_enabled=True, agentops_api_key=VALID_KEY)

        ObservabilityManager(settings).start()

        import os

        # Duplicate span export was a real defect in the prototype.
        assert os.environ["CREWAI_TRACING_ENABLED"] == "false"

    def test_respects_an_explicit_crewai_tracing_opt_in(
        self, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import os

        settings = make_settings(agentops_enabled=False, crewai_tracing_enabled=True)
        ObservabilityManager(settings).start()
        assert os.environ["CREWAI_TRACING_ENABLED"] == "true"

    def test_keeps_crewai_tracing_off_by_default(self, settings: Settings) -> None:
        import os

        ObservabilityManager(make_settings(crewai_tracing_enabled=False)).start()
        assert os.environ["CREWAI_TRACING_ENABLED"] == "false"


class TestLoggingConfiguration:
    def test_json_format_produces_structured_records(self, capsys: pytest.CaptureFixture[str]) -> None:
        configure_logging("INFO", "json", force=True)
        get_logger("tests.json").info("hello", extra={"request_id": "abc", "count": 2})

        lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
        payload = json.loads(lines[-1])
        assert payload["message"] == "hello"
        assert payload["level"] == "INFO"
        assert payload["logger"] == "tests.json"
        assert payload["request_id"] == "abc"
        assert payload["count"] == 2
        assert "timestamp" in payload

    def test_console_format_is_human_readable(self, capsys: pytest.CaptureFixture[str]) -> None:
        configure_logging("INFO", "console", force=True)
        get_logger("tests.console").warning("careful", extra={"product": "coffee machine"})

        output = capsys.readouterr().out
        assert "warning" in output.lower()
        assert "careful" in output
        assert "coffee machine" in output

    def test_configuration_is_idempotent(self) -> None:
        configure_logging("WARNING", "console", force=True)
        logger = logging.getLogger()
        handlers = list(logger.handlers)
        configure_logging("WARNING", "console")
        assert list(logger.handlers) == handlers

    def test_quiet_levels_are_applied(self) -> None:
        configure_logging("ERROR", "console", force=True)
        assert logging.getLogger().level == logging.ERROR
        configure_logging("DEBUG", "console", force=True)
        assert logging.getLogger().level == logging.DEBUG
        configure_logging("INFO", "console", force=True)
        assert logging.getLogger().level == logging.INFO

    def test_invalid_level_falls_back_to_info(self) -> None:
        configure_logging("not-a-level", "console", force=True)
        assert logging.getLogger().level == logging.INFO
