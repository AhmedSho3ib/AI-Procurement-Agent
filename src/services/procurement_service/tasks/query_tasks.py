"""Task 1 - suggest search queries (structured output)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.schemas.search import SuggestedSearchQueries
from src.services.procurement_service.prompts import PromptLibrary, get_prompt_library

__all__ = ["PROMPT_NAME", "build_query_task"]

PROMPT_NAME = "query_agent"


def build_query_task(
    agent: Any,
    *,
    variables: Mapping[str, Any],
    prompts: PromptLibrary | None = None,
    output_file: str | None = None,
    context: list[Any] | None = None,
) -> Any:
    """Create the search-query recommendation task with a strict JSON contract."""
    from crewai import Task

    library = prompts or get_prompt_library()
    sections = library.render(PROMPT_NAME, **variables)
    return Task(
        description=sections.task,
        expected_output=sections.expected_output,
        agent=agent,
        output_json=SuggestedSearchQueries,
        output_file=output_file,
        context=list(context or []),
    )
