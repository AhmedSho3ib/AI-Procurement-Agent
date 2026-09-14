"""CrewAI orchestration of the procurement pipeline.

This is the production workflow: four agents (query recommendation, search,
extraction, reporting) executed sequentially by a CrewAI ``Crew``.

Everything CrewAI specific lives behind this module. The service, controller and
API only see :class:`ProcurementWorkflow` and :class:`WorkflowResult`.
"""

from __future__ import annotations

from typing import Any

from src.schemas.procurement import ProcurementRequest, WorkflowResult
from src.schemas.products import Products
from src.schemas.reports import ProcurementReport
from src.schemas.search import SearchResults, SuggestedSearchQueries
from src.services.procurement_service.agents.extraction_agent import (
    build_extraction_agent,
    build_scraping_tool,
)
from src.services.procurement_service.agents.query_agent import build_query_agent
from src.services.procurement_service.agents.report_agent import build_report_agent
from src.services.procurement_service.agents.search_agent import build_search_agent, build_search_tool
from src.services.procurement_service.prompts import PromptLibrary, get_prompt_library
from src.services.procurement_service.tasks.extraction_tasks import build_extraction_task
from src.services.procurement_service.tasks.query_tasks import build_query_task
from src.services.procurement_service.tasks.report_tasks import build_report_task
from src.services.procurement_service.tasks.search_tasks import build_search_task
from src.services.procurement_service.workflow import (
    ProcurementWorkflow,
    WorkflowContext,
    parse_structured_output,
)
from src.stores.llm import LLMInterface
from src.stores.scraping import ScraperInterface
from src.stores.search import SearchInterface
from src.utils.config import Settings
from src.utils.exceptions import ConfigurationError, ProcurementError, classify_provider_error
from src.utils.logging import get_logger

__all__ = ["CrewAIProcurementWorkflow"]

logger = get_logger(__name__)


class CrewAIProcurementWorkflow(ProcurementWorkflow):
    """Sequential four-agent CrewAI workflow."""

    name = "crew"

    def __init__(
        self,
        *,
        llm: LLMInterface,
        search_store: SearchInterface,
        scraper_store: ScraperInterface,
        settings: Settings,
        prompts: PromptLibrary | None = None,
    ) -> None:
        self._llm = llm
        self._search_store = search_store
        self._scraper_store = scraper_store
        self._settings = settings
        self._prompts = prompts or get_prompt_library()

    def describe(self) -> dict[str, Any]:
        return {
            "workflow": self.name,
            "llm": self._llm.model_name,
            "search": self._search_store.describe(),
            "scraper": self._scraper_store.describe(),
        }

    # ------------------------------------------------------------------ builder
    def build_crew(self, context: WorkflowContext) -> tuple[Any, dict[str, Any]]:
        """Assemble the crew. Kept public so wiring can be verified in tests."""
        _require_crewai()
        request = context.request
        artifacts = context.artifacts()

        agent_options = self._agent_options()
        variables = context.prompt_variables
        llm_handle = self._llm.as_agent_llm()

        query_agent = build_query_agent(
            llm_handle,
            variables=variables,
            prompts=self._prompts,
            verbose=self._settings.debug,
            options=agent_options,
        )
        search_tool = build_search_tool(
            self._search_store,
            max_results=self._settings.search_max_results,
            score_threshold=request.score_threshold,
            allowed_domains=context.allowed_domains or None,
        )
        search_agent = build_search_agent(
            llm_handle,
            tools=[search_tool],
            variables=variables,
            prompts=self._prompts,
            verbose=self._settings.debug,
            options=agent_options,
        )
        scraping_tool = build_scraping_tool(self._scraper_store)
        extraction_agent = build_extraction_agent(
            llm_handle,
            tools=[scraping_tool],
            variables=variables,
            prompts=self._prompts,
            verbose=self._settings.debug,
            options=agent_options,
        )
        report_agent = build_report_agent(
            llm_handle,
            variables=variables,
            prompts=self._prompts,
            verbose=self._settings.debug,
            options=agent_options,
        )

        query_task = build_query_task(
            query_agent,
            variables=variables,
            prompts=self._prompts,
            output_file=artifacts["queries"],
        )
        search_task = build_search_task(
            search_agent,
            variables=variables,
            prompts=self._prompts,
            output_file=artifacts["search"],
            context=[query_task],
        )
        extraction_task = build_extraction_task(
            extraction_agent,
            variables=variables,
            prompts=self._prompts,
            output_file=artifacts["products"],
            context=[query_task, search_task],
        )
        report_task = build_report_task(
            report_agent,
            variables=variables,
            prompts=self._prompts,
            output_file=artifacts["report"],
            context=[query_task, search_task, extraction_task],
        )

        from crewai import Crew, Process

        crew = Crew(
            agents=[query_agent, search_agent, extraction_agent, report_agent],
            tasks=[query_task, search_task, extraction_task, report_task],
            process=Process.sequential,
            verbose=self._settings.debug,
        )
        named = {
            "query_task": query_task,
            "search_task": search_task,
            "extraction_task": extraction_task,
            "report_task": report_task,
        }
        return crew, named

    # ------------------------------------------------------------------ execute
    def execute(self, request: ProcurementRequest) -> WorkflowResult:
        context = WorkflowContext.from_request(request, self._settings)
        crew, tasks = self.build_crew(context)
        warnings = list(context.warnings)

        logger.info(
            "crew_started",
            extra={
                "product": request.product,
                "websites": len(request.websites),
                "max_queries": request.max_queries,
                "top_n": request.top_n,
            },
        )

        try:
            crew_output = crew.kickoff()
        except Exception as error:
            classified = classify_provider_error(
                error,
                provider=self._llm.provider.value,
                operation="crew.kickoff",
                context={"product": request.product},
            )
            logger.error(
                "crew_failed",
                extra={"error": classified.code, "status_code": classified.context.get("status_code")},
            )
            raise classified from error

        task_outputs = {name: getattr(task, "output", None) for name, task in tasks.items()}
        queries_model = parse_structured_output(
            task_outputs.get("query_task"), SuggestedSearchQueries, step="queries"
        )
        search_model = parse_structured_output(task_outputs.get("search_task"), SearchResults, step="search")
        products_model = parse_structured_output(
            task_outputs.get("extraction_task"), Products, step="extraction"
        )
        report_model = parse_structured_output(
            task_outputs.get("report_task"), ProcurementReport, step="report"
        )

        if queries_model is None:
            raise ProcurementError(
                "The search-query agent did not return usable queries",
                context={"product": request.product, "step": "queries"},
            )
        if search_model is None:
            warnings.append("The search agent returned no parsable results.")
        if products_model is None:
            warnings.append("The extraction agent returned no parsable products.")
        if report_model is None:
            warnings.append("The report agent returned no parsable report content.")

        queries = queries_model.queries[: request.max_queries]
        search_results = search_model.results if search_model else []
        products = products_model.products if products_model else []

        logger.info(
            "crew_completed",
            extra={
                "queries": len(queries),
                "search_results": len(search_results),
                "products": len(products),
                "report": report_model is not None,
            },
        )

        token_usage = getattr(crew_output, "token_usage", None)
        return WorkflowResult(
            queries=queries,
            search_results=search_results,
            products=products,
            report=report_model,
            warnings=warnings,
            metadata={
                "workflow": self.name,
                "llm_model": self._llm.model_name,
                "token_usage": _token_usage_summary(token_usage),
                "artifacts": {key: value for key, value in context.artifacts().items() if value},
            },
        )

    # ------------------------------------------------------------------ helpers
    def _agent_options(self) -> dict[str, Any]:
        """Bounded execution limits so an agent can never hang a request forever."""
        budget = max(self._settings.request_timeout_seconds * 4, 60.0)
        return {
            "max_execution_time": budget,
            "max_retry_limit": max(1, self._settings.max_retries - 1),
            "max_iter": 15,
        }


def _token_usage_summary(token_usage: Any) -> dict[str, Any] | None:
    if token_usage is None:
        return None
    if hasattr(token_usage, "model_dump"):
        return dict(token_usage.model_dump())
    if isinstance(token_usage, dict):
        return token_usage
    return None


def _require_crewai() -> None:
    """Fail with an actionable error when CrewAI is not importable."""
    try:
        import crewai  # noqa: F401
    except ImportError as error:  # pragma: no cover - crewai is a runtime dependency
        raise ConfigurationError(
            "WORKFLOW=crew requires the 'crewai' package to be installed",
            context={"hint": "pip install 'crewai>=1.15' or set WORKFLOW=sequential"},
        ) from error
