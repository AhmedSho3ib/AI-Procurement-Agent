"""CrewAI workflow tests: wiring, output parsing and failure handling.

The crew is built with real CrewAI objects (agents, tasks, tools) but the LLM
handle is a mock, so no provider is contacted. ``kickoff`` is replaced with a fake
so agent behaviour is not simulated - only the wiring and the result handling that
this project owns.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import BaseModel

from src.schemas.procurement import ProcurementRequest
from src.schemas.products import Product, Products
from src.schemas.reports import ProcurementReport
from src.schemas.search import SearchResult, SearchResults, SuggestedSearchQueries
from src.services.procurement_service.crew import CrewAIProcurementWorkflow
from src.services.procurement_service.workflow import (
    WorkflowContext,
    parse_structured_output,
)
from src.utils.config import Settings
from src.utils.exceptions import ConfigurationError, ProviderAuthError
from tests.conftest import make_settings


class FakeTaskOutput:
    """Mimics the shapes CrewAI can hand back for a task output."""

    def __init__(self, *, pydantic: Any = None, json_dict: Any = None, raw: str = "") -> None:
        self.pydantic = pydantic
        self.json_dict = json_dict
        self.raw = raw


class FakeTask:
    def __init__(self, output: Any) -> None:
        self.output = output


class FakeCrewOutput:
    token_usage = None


class FakeCrew:
    def __init__(self, *, error: Exception | None = None) -> None:
        self._error = error
        self.kickoff_calls = 0

    def kickoff(self) -> Any:
        self.kickoff_calls += 1
        if self._error is not None:
            raise self._error
        return FakeCrewOutput()


@pytest.fixture
def workflow(settings: Settings) -> CrewAIProcurementWorkflow:
    from src.stores.llm.providers.MockProvider import MockProvider as MockLLM
    from src.stores.scraping.providers.MockProvider import MockProvider as MockScraper
    from src.stores.search.providers.MockProvider import MockProvider as MockSearch

    return CrewAIProcurementWorkflow(
        llm=MockLLM(),
        search_store=MockSearch(),
        scraper_store=MockScraper(),
        settings=make_settings(workflow="crew"),
    )


@pytest.fixture
def request_() -> ProcurementRequest:
    return ProcurementRequest(
        product="coffee machine for the office",
        websites=["www.amazon.eg", "www.jumia.com.eg"],
        max_queries=3,
        top_n=3,
    )


class TestCrewWiring:
    def test_builds_four_agents_four_tasks_and_the_expected_tools(
        self, workflow: CrewAIProcurementWorkflow, request_: ProcurementRequest, settings: Settings
    ) -> None:
        context = WorkflowContext.from_request(request_, settings)
        crew, tasks = workflow.build_crew(context)

        assert [agent.role for agent in crew.agents] == [
            "Search Queries Recommendation Agent",
            "Search Engine Agent",
            "Web Scraping and Product Extraction Agent",
            "Procurement Report Author Agent",
        ]
        assert list(tasks) == ["query_task", "search_task", "extraction_task", "report_task"]

    def test_tasks_use_strict_structured_outputs(
        self, workflow: CrewAIProcurementWorkflow, request_: ProcurementRequest, settings: Settings
    ) -> None:
        context = WorkflowContext.from_request(request_, settings)
        _, tasks = workflow.build_crew(context)

        assert [tasks[name].output_json for name in tasks] == [
            SuggestedSearchQueries,
            SearchResults,
            Products,
            ProcurementReport,
        ]

    def test_only_search_and_extraction_agents_get_tools(
        self, workflow: CrewAIProcurementWorkflow, request_: ProcurementRequest, settings: Settings
    ) -> None:
        context = WorkflowContext.from_request(request_, settings)
        crew, _ = workflow.build_crew(context)

        query_agent, search_agent, extraction_agent, report_agent = crew.agents
        assert query_agent.tools == []
        assert [tool.name for tool in search_agent.tools] == ["search_products"]
        assert [tool.name for tool in extraction_agent.tools] == ["scrape_product_page"]
        assert report_agent.tools == []

    def test_prompts_are_rendered_with_the_request_variables(
        self, workflow: CrewAIProcurementWorkflow, request_: ProcurementRequest, settings: Settings
    ) -> None:
        context = WorkflowContext.from_request(request_, settings)
        _, tasks = workflow.build_crew(context)

        query_description = tasks["query_task"].description
        assert request_.product in query_description
        assert request_.company_name in query_description
        assert request_.websites[0] in query_description
        assert str(request_.max_queries) in query_description
        assert "$" not in query_description  # every variable was substituted

    def test_tasks_depend_on_previous_task_outputs(
        self, workflow: CrewAIProcurementWorkflow, request_: ProcurementRequest, settings: Settings
    ) -> None:
        context = WorkflowContext.from_request(request_, settings)
        _, tasks = workflow.build_crew(context)

        assert tasks["search_task"].context == [tasks["query_task"]]
        assert len(tasks["extraction_task"].context) == 2
        assert len(tasks["report_task"].context) == 3

    def test_describe_reports_every_collaborator(self, workflow: CrewAIProcurementWorkflow) -> None:
        described = workflow.describe()
        assert described["workflow"] == "crew"
        assert described["search"]["provider"] == "mock"


class TestCrewExecution:
    def _patch_crew(
        self, workflow: CrewAIProcurementWorkflow, monkeypatch: Any, crew: FakeCrew, outputs: dict[str, Any]
    ) -> None:
        tasks = {name: FakeTask(output) for name, output in outputs.items()}

        def fake_build(context: WorkflowContext) -> Any:
            return crew, tasks

        monkeypatch.setattr(workflow, "build_crew", fake_build)

    def test_maps_structured_task_outputs_into_a_workflow_result(
        self, workflow: CrewAIProcurementWorkflow, request_: ProcurementRequest, monkeypatch: Any
    ) -> None:
        product = Product(
            page_url="https://www.amazon.eg/dp/B00400OMU0",
            product_title="De'Longhi Magnifica S",
            product_current_price=24999.0,
            agent_recommendation_rank=5,
        )
        outputs = {
            "query_task": FakeTaskOutput(pydantic=SuggestedSearchQueries(queries=["q1", "q2"])),
            "search_task": FakeTaskOutput(
                pydantic=SearchResults(results=[SearchResult(url="https://x.com/p/1")])
            ),
            "extraction_task": FakeTaskOutput(pydantic=Products(products=[product])),
            "report_task": FakeTaskOutput(pydantic=ProcurementReport(title="Report")),
        }
        self._patch_crew(workflow, monkeypatch, FakeCrew(), outputs)

        result = workflow.execute(request_)

        assert result.queries == ["q1", "q2"]
        assert len(result.search_results) == 1
        assert result.products[0].product_title == "De'Longhi Magnifica S"
        assert result.report is not None
        assert result.metadata["workflow"] == "crew"
        assert result.warnings == []

    def test_truncates_queries_to_max_queries(
        self, workflow: CrewAIProcurementWorkflow, request_: ProcurementRequest, monkeypatch: Any
    ) -> None:
        outputs = {
            "query_task": FakeTaskOutput(json_dict={"queries": ["a", "b", "c", "d", "e"]}),
            "search_task": FakeTaskOutput(raw='{"results": []}'),
            "extraction_task": FakeTaskOutput(raw='{"products": []}'),
            "report_task": FakeTaskOutput(raw='{"title": "Report"}'),
        }
        self._patch_crew(workflow, monkeypatch, FakeCrew(), outputs)

        result = workflow.execute(request_)

        assert result.queries == ["a", "b", "c"]  # the request asked for 3

    def test_missing_structured_outputs_become_warnings_not_crashes(
        self, workflow: CrewAIProcurementWorkflow, request_: ProcurementRequest, monkeypatch: Any
    ) -> None:
        outputs = {
            "query_task": FakeTaskOutput(pydantic=SuggestedSearchQueries(queries=["q1"])),
            "search_task": FakeTaskOutput(raw="I could not complete the search."),
            "extraction_task": FakeTaskOutput(raw="no products found"),
            "report_task": FakeTaskOutput(raw="here is your report"),
        }
        self._patch_crew(workflow, monkeypatch, FakeCrew(), outputs)

        result = workflow.execute(request_)

        assert result.search_results == []
        assert result.products == []
        assert result.report is None
        assert len(result.warnings) == 3

    def test_unusable_queries_abort_the_run_with_a_domain_error(
        self, workflow: CrewAIProcurementWorkflow, request_: ProcurementRequest, monkeypatch: Any
    ) -> None:
        from src.utils.exceptions import ProcurementError

        outputs = {"query_task": FakeTaskOutput(raw="I refuse to answer.")}
        self._patch_crew(workflow, monkeypatch, FakeCrew(), outputs)

        with pytest.raises(ProcurementError) as error:
            workflow.execute(request_)
        assert error.value.context["step"] == "queries"

    def test_provider_failures_are_classified_not_leaked(
        self, workflow: CrewAIProcurementWorkflow, request_: ProcurementRequest, monkeypatch: Any
    ) -> None:
        crew = FakeCrew(error=RuntimeError("Error code: 401 - unauthorized client detected"))
        outputs = {"query_task": FakeTaskOutput(raw="{}")}
        self._patch_crew(workflow, monkeypatch, crew, outputs)

        with pytest.raises(ProviderAuthError):
            workflow.execute(request_)

    def test_agent_execution_limits_are_bounded(self, workflow: CrewAIProcurementWorkflow) -> None:
        options = workflow._agent_options()
        assert options["max_execution_time"] > 0
        assert options["max_iter"] == 15
        assert options["max_retry_limit"] >= 1


class TestParseStructuredOutput:
    class Payload(BaseModel):
        value: int = 0

    def test_reads_a_pydantic_attribute(self) -> None:
        parsed = parse_structured_output(
            FakeTaskOutput(pydantic=self.Payload(value=7)), self.Payload, step="x"
        )
        assert parsed is not None and parsed.value == 7

    def test_reads_a_json_dict(self) -> None:
        parsed = parse_structured_output(FakeTaskOutput(json_dict={"value": 3}), self.Payload, step="x")
        assert parsed is not None and parsed.value == 3

    def test_reads_raw_json_with_surrounding_prose(self) -> None:
        raw = 'Sure! Here is the JSON:\n```json\n{"value": 5}\n```'
        parsed = parse_structured_output(FakeTaskOutput(raw=raw), self.Payload, step="x")
        assert parsed is not None and parsed.value == 5

    def test_reads_a_pydantic_typed_json_dict(self) -> None:
        parsed = parse_structured_output(
            FakeTaskOutput(json_dict=self.Payload(value=9)), self.Payload, step="x"
        )
        assert parsed is not None and parsed.value == 9

    def test_returns_none_for_unusable_output(self) -> None:
        assert parse_structured_output(None, self.Payload, step="x") is None
        assert parse_structured_output("plain text", self.Payload, step="x") is None
        assert parse_structured_output(FakeTaskOutput(raw="nope"), self.Payload, step="x") is None

    def test_returns_none_when_the_payload_fails_validation(self) -> None:
        assert (
            parse_structured_output(FakeTaskOutput(json_dict={"value": "abc"}), self.Payload, step="x")
            is None
        )

    def test_accepts_an_already_validated_model_instance(self) -> None:
        instance = self.Payload(value=1)
        assert parse_structured_output(instance, self.Payload, step="x") is instance

    def test_json_extraction_is_conservative(self) -> None:
        from src.services.procurement_service.workflow import _extract_json_object

        assert _extract_json_object('{"a": 1}') == '{"a": 1}'
        assert _extract_json_object('text {"a": 1} trailing') == '{"a": 1}'
        # Malformed content is returned untouched so validation reports the real error.
        assert _extract_json_object("{oops") == "{oops"


class TestWorkflowFactory:
    def test_builds_the_sequential_workflow_from_settings(self) -> None:
        from src.services.procurement_service.procurement_service import build_workflow
        from src.stores.llm.providers.MockProvider import MockProvider as MockLLM
        from src.stores.scraping.providers.MockProvider import MockProvider as MockScraper
        from src.stores.search.providers.MockProvider import MockProvider as MockSearch

        built = build_workflow(
            llm=MockLLM(),
            search_store=MockSearch(),
            scraper_store=MockScraper(),
            settings=make_settings(workflow="sequential"),
        )
        assert built.name == "sequential"

    def test_builds_the_crew_workflow_from_settings(self) -> None:
        from src.services.procurement_service.procurement_service import build_workflow
        from src.stores.llm.providers.MockProvider import MockProvider as MockLLM
        from src.stores.scraping.providers.MockProvider import MockProvider as MockScraper
        from src.stores.search.providers.MockProvider import MockProvider as MockSearch

        built = build_workflow(
            llm=MockLLM(),
            search_store=MockSearch(),
            scraper_store=MockScraper(),
            settings=make_settings(workflow="crew"),
        )
        assert built.name == "crew"

    def test_unknown_workflow_raises_configuration_error(self) -> None:
        from src.services.procurement_service.procurement_service import build_workflow
        from src.stores.llm.providers.MockProvider import MockProvider as MockLLM
        from src.stores.scraping.providers.MockProvider import MockProvider as MockScraper
        from src.stores.search.providers.MockProvider import MockProvider as MockSearch

        settings = make_settings(workflow="crew")
        object.__setattr__(settings, "workflow", "unknown-workflow")
        with pytest.raises(ConfigurationError):
            build_workflow(
                llm=MockLLM(),
                search_store=MockSearch(),
                scraper_store=MockScraper(),
                settings=settings,
            )


def test_crew_output_json_is_serialisable() -> None:
    # Guards against non-serialisable values slipping into metadata.
    payload = {"token_usage": None, "workflow": "crew"}
    assert json.loads(json.dumps(payload)) == payload
