"""Task 4 - author the procurement report content (structured output).

The task returns structured content, not HTML. Rendering belongs to the
reporting store, which is why the ``[TASK]`` prompt explicitly forbids HTML.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.schemas.reports import ProcurementReport
from src.services.procurement_service.prompts import PromptLibrary, get_prompt_library

__all__ = ["PROMPT_NAME", "build_report_task"]

PROMPT_NAME = "report_agent"


def build_report_task(
    agent: Any,
    *,
    variables: Mapping[str, Any],
    prompts: PromptLibrary | None = None,
    output_file: str | None = None,
    context: list[Any] | None = None,
) -> Any:
    """Create the report authoring task."""
    from crewai import Task

    library = prompts or get_prompt_library()
    sections = library.render(PROMPT_NAME, **variables)
    return Task(
        description=sections.task,
        expected_output=sections.expected_output,
        agent=agent,
        output_json=ProcurementReport,
        output_file=output_file,
        context=list(context or []),
    )
