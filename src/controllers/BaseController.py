"""Shared controller behaviour.

Controllers are the orchestration seam between the delivery layer (routes) and
the domain layer (services). They are deliberately thin and stateless: they hold
their collaborators, delegate, and return response schemas. No business rules,
no provider calls, no SQL.
"""

from __future__ import annotations

from src.utils.config import Settings

__all__ = ["BaseController"]


class BaseController:
    """Base class providing common controller state (configuration access)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def settings(self) -> Settings:
        """Read-only access to application configuration."""
        return self._settings
