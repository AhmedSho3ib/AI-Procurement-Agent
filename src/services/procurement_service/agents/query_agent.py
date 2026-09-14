"""Agent 1 - Search Queries Recommendation Agent.

Persona and instructions are defined in ``prompts/query_agent.md``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.services.procurement_service.prompts import PromptLibrary, get_prompt_library

__all__ = ["PROMPT_NAME", "build_query_agent"]

PROMPT_NAME = "query_agent"


def build_query_agent(
    llm_handle: Any,
    *,
    variables: Mapping[str, Any],
    prompts: PromptLibrary | None = None,
    verbose: bool = False,
    options: Mapping[str, Any] | None = None,
) -> Any:
    """Create the search queries recommendation agent.

    Args:
        llm_handle: Provider native handle obtained from ``LLMInterface.as_agent_llm()``.
        variables: Values injected into the prompt file.
        prompts: Prompt library override (used by tests).
        verbose: CrewAI verbose flag.
        options: Extra CrewAI agent options (execution limits, retry limits...).

    Returns:
        A configured ``crewai.Agent``.
    """
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
