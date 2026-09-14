"""Agent 3 - Web Scraping / Product Extraction Agent (and its scraping tool)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from src.services.procurement_service.prompts import PromptLibrary, get_prompt_library
from src.stores.scraping import ScraperInterface
from src.utils.exceptions import AppError
from src.utils.logging import get_logger

__all__ = ["PROMPT_NAME", "build_extraction_agent", "build_scraping_tool"]

PROMPT_NAME = "extraction_agent"

logger = get_logger(__name__)


def build_scraping_tool(scraper_store: ScraperInterface) -> Any:
    """Build the CrewAI tool exposed to the extraction agent."""
    from crewai.tools import tool

    @tool("scrape_product_page")
    def scrape_product_page(page_url: str) -> str:
        """Scrape a single ecommerce product page and return structured product data.

        Args:
            page_url: The absolute URL of a product detail page.

        Returns:
            JSON with the extracted product, or an "error" key when the page failed.
        """
        try:
            product = scraper_store.extract_product(page_url)
        except AppError as error:
            # Partial failures are expected when scraping many pages: report them
            # to the agent so it can continue with the remaining URLs.
            logger.warning(
                "scraping_tool_failed",
                extra={"url": page_url, "error": error.code},
            )
            return json.dumps({"page_url": page_url, "error": error.message})
        return json.dumps({"page_url": page_url, "product": product.model_dump()})

    return scrape_product_page


def build_extraction_agent(
    llm_handle: Any,
    *,
    tools: list[Any],
    variables: Mapping[str, Any],
    prompts: PromptLibrary | None = None,
    verbose: bool = False,
    options: Mapping[str, Any] | None = None,
) -> Any:
    """Create the product extraction agent that owns the scraping tool."""
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
