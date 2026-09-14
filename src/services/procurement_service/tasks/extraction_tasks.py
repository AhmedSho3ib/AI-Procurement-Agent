"""Task 3 - extract and rank products from the discovered pages."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.schemas.products import Products
from src.services.procurement_service.prompts import PromptLibrary, get_prompt_library

__all__ = ["PROMPT_NAME", "build_extraction_task"]

PROMPT_NAME = "extraction_agent"


def build_extraction_task(
    agent: Any,
    *,
    variables: Mapping[str, Any],
    prompts: PromptLibrary | None = None,
    output_file: str | None = None,
    context: list[Any] | None = None,
) -> Any:
    """Create the scraping/product-extraction task."""
    from crewai import Task

    library = prompts or get_prompt_library()
    sections = library.render(PROMPT_NAME, **variables)
    return Task(
        description=sections.task,
        expected_output=sections.expected_output,
        agent=agent,
        output_json=Products,
        output_file=output_file,
        context=list(context or []),
    )
