"""Task 2 - execute the queries through the search tool (structured output)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.schemas.search import SearchResults
from src.services.procurement_service.prompts import PromptLibrary, get_prompt_library

__all__ = ["PROMPT_NAME", "build_search_task"]

PROMPT_NAME = "search_agent"


def build_search_task(
    agent: Any,
    *,
    variables: Mapping[str, Any],
    prompts: PromptLibrary | None = None,
    output_file: str | None = None,
    context: list[Any] | None = None,
) -> Any:
    """Create the search execution task.

    ``context`` carries the previous task output so the agent sees the suggested
    queries as data rather than having to re-derive them.
    """
    from crewai import Task

    library = prompts or get_prompt_library()
    sections = library.render(PROMPT_NAME, **variables)
    return Task(
        description=sections.task,
        expected_output=sections.expected_output,
        agent=agent,
        output_json=SearchResults,
        output_file=output_file,
        context=list(context or []),
    )
