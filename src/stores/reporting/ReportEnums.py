"""Reporting provider identifiers."""

from __future__ import annotations

from enum import StrEnum

__all__ = ["ReportProvider"]


class ReportProvider(StrEnum):
    """Supported report renderers."""

    JINJA = "jinja"
    MOCK = "mock"
