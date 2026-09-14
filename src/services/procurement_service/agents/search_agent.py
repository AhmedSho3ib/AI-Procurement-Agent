"""Agent 2 - Search Engine Agent (and its store-backed search tool).

The tool is the *only* path from the agent to the search provider: the agent never
talks to Tavily (or anything else) directly. Deterministic URL/score rules are
applied inside the tool so the model receives pre-cleaned observations.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from src.services.procurement_service.prompts import PromptLibrary, get_prompt_library
from src.services.procurement_service.rules.product_filtering import filter_search_results
from src.stores.search import SearchInterface
from src.utils.logging import get_logger

__all__ = ["PROMPT_NAME", "build_search_agent", "build_search_tool"]

PROMPT_NAME = "search_agent"

logger = get_logger(__name__)


def build_search_tool(
    search_store: SearchInterface,
    *,
    max_results: int,
    score_threshold: float,
    allowed_domains: frozenset[str] | None = None,
) -> Any:
    """Build the CrewAI tool exposed to the search agent."""
    from crewai.tools import tool

    @tool("search_products")
    def search_products(query: str) -> str:
        """Search the web for products using a search engine.

        Args:
            query: A single search query, typically one of the suggested queries.

        Returns:
            JSON with a "results" list of validated search results.
        """
        raw_results = search_store.search(query, max_results=max_results)
        outcome = filter_search_results(
            raw_results,
            score_threshold=score_threshold,
            allowed_domains=allowed_domains,
        )
        logger.debug(
            "search_tool_completed",
            extra={"query": query, "returned": len(raw_results), "kept": len(outcome.kept)},
        )
        payload = {
            "query": query,
            "results": [result.model_dump() for result in outcome.kept],
            "rejected": outcome.rejected_count,
        }
        return json.dumps(payload)

    return search_products


def build_search_agent(
    llm_handle: Any,
    *,
    tools: list[Any],
    variables: Mapping[str, Any],
    prompts: PromptLibrary | None = None,
    verbose: bool = False,
    options: Mapping[str, Any] | None = None,
) -> Any:
    """Create the search engine agent that owns the search tool."""
    from crewai import Agent

    library = prompts or get_prompt_library()
    sections = library.render(PROMPT_NAME, **variables)
    return Agent(
        role=sections.role,
        goal=sections.goal,
        backstory=sections.backstory,
        llm=llm_handle,
        tools=list(tools),
        verbose=verbose,
        **dict(options or {}),
    )
