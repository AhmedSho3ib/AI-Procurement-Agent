"""Prompt library behaviour: loading, rendering and failing loudly on mistakes."""

from __future__ import annotations

import pytest

from src.services.procurement_service.prompts import PromptLibrary
from src.utils.exceptions import ConfigurationError


def test_loads_all_four_agent_prompts(prompt_library: PromptLibrary) -> None:
    assert prompt_library.available() == [
        "extraction_agent",
        "query_agent",
        "report_agent",
        "search_agent",
    ]


def test_parses_every_section(prompt_library: PromptLibrary) -> None:
    sections = prompt_library.sections("query_agent")
    assert sections.role == "Search Queries Recommendation Agent"
    assert "suggested search queries" in sections.goal.lower()
    assert sections.backstory
    assert sections.task
    assert sections.expected_output


def test_renders_variables_into_the_task(prompt_library: PromptLibrary) -> None:
    sections = prompt_library.render(
        "query_agent",
        product="coffee machine",
        company_name="Rankyx",
        websites="www.amazon.eg",
        country="Egypt",
        language="English",
        max_queries=5,
    )
    assert "coffee machine" in sections.task
    assert "Rankyx" in sections.task
    assert "www.amazon.eg" in sections.task
    assert "$max_queries" not in sections.task
    assert "5 queries" in sections.task


def test_unresolved_variables_raise_instead_of_reaching_the_llm(prompt_library: PromptLibrary) -> None:
    with pytest.raises(ConfigurationError) as error:
        prompt_library.render("query_agent", product="coffee machine")
    assert "unresolved" in error.value.context
    assert "product" not in error.value.context["unresolved"]


def test_missing_prompt_file_raises_configuration_error(prompt_library: PromptLibrary) -> None:
    with pytest.raises(ConfigurationError) as error:
        prompt_library.sections("does_not_exist")
    assert error.value.context["available"]


def test_missing_directory_raises_configuration_error(tmp_path: object) -> None:
    with pytest.raises(ConfigurationError):
        PromptLibrary("Z:/definitely/not/here")


def test_incomplete_prompt_file_is_rejected(tmp_path) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / "broken.md").write_text("[ROLE]\nOnly a role\n", encoding="utf-8")
    with pytest.raises(ConfigurationError) as error:
        PromptLibrary(tmp_path).sections("broken")
    assert sorted(error.value.context["missing"]) == ["EXPECTED_OUTPUT", "TASK"]


def test_literal_json_braces_are_preserved(prompt_library: PromptLibrary) -> None:
    # Prompts use $variables precisely so JSON braces need no escaping.
    sections = prompt_library.render(
        "report_agent",
        company_name="Rankyx",
        product="coffee machine",
        websites="www.amazon.eg",
        country="Egypt",
        language="English",
    )
    assert "recommendations (list of strings)" in sections.expected_output
