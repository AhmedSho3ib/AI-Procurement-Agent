"""Agent 4 - Procurement Report Author Agent.

The agent produces *content* (:class:`~src.schemas.reports.ProcurementReport`).
Presentation is owned by the reporting store and ``templates/``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.services.procurement_service.prompts import PromptLibrary, get_prompt_library

__all__ = ["PROMPT_NAME", "build_report_agent"]

PROMPT_NAME = "report_agent"


def build_report_agent(
    llm_handle: Any,
    *,
    variables: Mapping[str, Any],
    prompts: PromptLibrary | None = None,
    verbose: bool = False,
    options: Mapping[str, Any] | None = None,
) -> Any:
    """Create the procurement report author agent (no tools required)."""
    from crewai import Agent

    library = prompts or get_prompt_library()
    sections = library.render(PROMPT_NAME, **variables)
    return Agent(
        role=sections.role,
        goal=sections.goal,
        backstory=sections.backstory,
        llm=llm_handle,
        verbose=verbose,
        **dict(options or {}),
    )
