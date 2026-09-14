"""Optional, fail-safe observability integration (AgentOps).

Design rules enforced here:

1. **Optional** - disabled by default. Enabling telemetry must be an explicit
   operator decision, never a hard requirement for the procurement workflow.
2. **Fail-safe** - any failure while initialising or closing the observability
   session is logged and swallowed. Observability must never take down a
   business request.
3. **Isolated** - this is the only module in the codebase that imports
   ``agentops``. Routes, services and stores stay vendor neutral.
4. **No duplicate exporters** - CrewAI ships its own OTLP exporter
   (``CREWAI_TRACING_ENABLED``). When AgentOps is managed here, that exporter is
   explicitly disabled so spans are not exported twice.
5. **Never fake success** - when initialisation fails we report ``False`` and a
   clear warning; we do not pretend telemetry is working.
"""

from __future__ import annotations

import os
from types import ModuleType
from typing import Any

from src.utils.config import Settings
from src.utils.logging import get_logger

__all__ = ["ObservabilityManager"]

logger = get_logger(__name__)

#: AgentOps requires a UUID shaped API key; anything else is rejected by the
#: auth endpoint with a 401 ``unauthorized_client_error``.
_AGENTOPS_KEY_HINT = "AgentOps API keys are UUIDs (8-4-4-4-12 hex characters)."


class ObservabilityManager:
    """Thin, defensive wrapper around the AgentOps SDK session lifecycle."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: ModuleType | None = None
        self._started = False
        self._last_error: str | None = None

    # ------------------------------------------------------------------ state
    @property
    def enabled(self) -> bool:
        return self._settings.agentops_enabled

    @property
    def active(self) -> bool:
        """``True`` only when telemetry is enabled *and* really initialised."""
        return self._started

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def describe(self) -> dict[str, Any]:
        """Secret-free status used by ``/health`` and startup logs."""
        return {
            "provider": "agentops",
            "enabled": self.enabled,
            "active": self.active,
            "last_error": self._last_error,
        }

    # -------------------------------------------------------------- lifecycle
    def start(self) -> bool:
        """Initialise the AgentOps session. Returns ``True`` on success."""
        self._last_error = None
        self._apply_tracing_environment()

        if not self.enabled:
            logger.info("agentops_disabled", extra={"hint": "set AGENTOPS_ENABLED=true to export traces"})
            return False

        api_key = self._settings.agentops_api_key
        if api_key is None or not api_key.get_secret_value().strip():
            self._last_error = "AGENTOPS_ENABLED=true but AGENTOPS_API_KEY is empty"
            logger.warning("agentops_not_started", extra={"reason": self._last_error})
            return False

        key = api_key.get_secret_value().strip()
        if not _looks_like_uuid(key):
            # Not fatal: the SDK also validates, but we surface the likely cause
            # of a 401 before it happens.
            logger.warning(
                "agentops_api_key_shape_suspicious",
                extra={"hint": _AGENTOPS_KEY_HINT},
            )

        try:
            import agentops  # imported lazily so the dependency stays optional

            self._client = agentops

            agentops.init(
                api_key=key,
                skip_auto_end_session=self._settings.agentops_skip_auto_end_session,
                default_tags=self._settings.agentops_tags,
                log_level=self._settings.log_level,
                fail_safe=True,
            )
        except Exception as error:  # noqa: BLE001 - telemetry must never break the app
            self._last_error = f"{type(error).__name__}: {error}"
            logger.warning("agentops_init_failed", extra={"error": self._last_error})
            return False

        self._started = True
        logger.info(
            "agentops_started",
            extra={
                "tags": self._settings.agentops_tags,
                "session_managed": not self._settings.agentops_skip_auto_end_session,
            },
        )
        return True

    def end(self, status: str = "Success") -> None:
        """Close the AgentOps session. Safe to call multiple times."""
        if not self._started or self._client is None:
            return
        try:
            self._client.end_session(status)
            logger.info("agentops_session_ended", extra={"status": status})
        except Exception as error:  # noqa: BLE001 - never raise on shutdown
            self._last_error = f"{type(error).__name__}: {error}"
            logger.warning("agentops_end_session_failed", extra={"error": self._last_error})
        finally:
            self._started = False

    # ----------------------------------------------------------------- helpers
    def _apply_tracing_environment(self) -> None:
        """Prevent duplicate span export when this manager owns the session.

        CrewAI's built-in exporter targets the same AgentOps OTLP endpoint, which
        produced duplicated spans (and duplicated errors) in the prototype.
        """
        if self.enabled:
            os.environ["CREWAI_TRACING_ENABLED"] = "false"
        else:
            os.environ["CREWAI_TRACING_ENABLED"] = (
                "true" if self._settings.crewai_tracing_enabled else "false"
            )


def _looks_like_uuid(value: str) -> bool:
    parts = value.split("-")
    if len(parts) != 5:
        return False
    expected = (8, 4, 4, 4, 12)
    if tuple(len(part) for part in parts) != expected:
        return False
    allowed = set("0123456789abcdefABCDEF")
    return all(all(char in allowed for char in part) for part in parts)
