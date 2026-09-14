"""CrewAI agent factories.

Each module in this package builds exactly one agent from a prompt file and, when
required, the store-backed tools that agent is allowed to use. Agents never
import providers or SDKs directly.
"""

from src.services.procurement_service.agents.extraction_agent import (
    build_extraction_agent,
    build_scraping_tool,
)
from src.services.procurement_service.agents.query_agent import build_query_agent
from src.services.procurement_service.agents.report_agent import build_report_agent
from src.services.procurement_service.agents.search_agent import build_search_agent, build_search_tool

__all__ = [
    "build_extraction_agent",
    "build_query_agent",
    "build_report_agent",
    "build_scraping_tool",
    "build_search_agent",
    "build_search_tool",
]
