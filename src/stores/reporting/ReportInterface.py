"""The report renderer contract.

The LLM produces :class:`~src.schemas.reports.ProcurementReport` content; a
renderer turns that content plus deterministic context into an artifact. Keeping
presentation here (and in ``templates/``) is what removed the giant HTML string
from the prototype's agent code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from src.schemas.reports import ProcurementReport, RenderedReport, ReportContext, ReportFormat
from src.stores.reporting.ReportEnums import ReportProvider

__all__ = ["ReportInterface"]


class ReportInterface(ABC):
    """Renders a procurement report."""

    provider: ReportProvider

    @abstractmethod
    def render(
        self,
        report: ProcurementReport,
        context: ReportContext,
        report_format: ReportFormat = ReportFormat.HTML,
    ) -> RenderedReport:
        """Render ``report`` (content) against ``context`` (data) in ``report_format``.

        Raises:
            ReportGenerationError: the format is unsupported or rendering failed.
        """

    def describe(self) -> dict[str, Any]:
        """Secret-free provider information for health checks and traces."""
        return {"provider": self.provider.value}
