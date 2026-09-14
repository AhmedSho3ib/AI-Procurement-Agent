"""Workflow contract shared by every procurement orchestration strategy.

Two strategies implement it:

``CrewAIProcurementWorkflow`` (``crew.py``)
    The production path: four CrewAI agents collaborating sequentially.

``SequentialProcurementWorkflow`` (``sequential.py``)
    A deterministic, credential-free executor that runs the same steps through the
    same stores and rules. Used for CI, local development and tests.

Both return the same :class:`WorkflowResult`, so the service, controller and API
cannot tell which one ran. That is what makes the workflow swappable and the
service testable without API keys.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from src.schemas.procurement import ProcurementRequest, WorkflowResult
from src.services.procurement_service.rules.url_validation import extract_domain
from src.utils.config import Settings
from src.utils.logging import get_logger

__all__ = [
    "ARTIFACT_FILES",
    "ProcurementWorkflow",
    "WorkflowContext",
    "build_prompt_variables",
    "parse_structured_output",
]
logger = get_logger(__name__)

#: Step artifacts written by the workflows, mirroring the prototype's
#: ``ai-agent-output/step_*.json`` files.
ARTIFACT_FILES: dict[str, str] = {
    "queries": "step_1_suggested_search_queries.json",
    "search": "step_2_search_results.json",
    "products": "step_3_extracted_products.json",
    "report": "step_4_report_content.json",
}

StructuredModelT = TypeVar("StructuredModelT", bound=BaseModel)


class ProcurementWorkflow(ABC):
    """A strategy for executing the procurement research pipeline."""

    #: Stable identifier reported in API metadata and logs.
    name: str = "workflow"

    @abstractmethod
    def execute(self, request: ProcurementRequest) -> WorkflowResult:
        """Run the workflow and return raw structured output."""

    def describe(self) -> dict[str, Any]:
        return {"workflow": self.name}


@dataclass(slots=True)
class WorkflowContext:
    """Inputs derived once and shared by both workflow implementations."""

    request: ProcurementRequest
    allowed_domains: frozenset[str]
    prompt_variables: dict[str, Any]
    output_dir: Path | None = None
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def from_request(cls, request: ProcurementRequest, settings: Settings) -> WorkflowContext:
        allowed_domains = frozenset(
            domain for domain in (extract_domain(site) for site in request.websites) if domain
        )
        warnings: list[str] = []
        if not allowed_domains:
            warnings.append(
                "No machine-readable domains in 'websites'; domain allow-listing was skipped "
                "and relevance relies on the search provider."
            )
        output_dir = settings.report_output_path if settings.report_output_dir else None
        return cls(
            request=request,
            allowed_domains=allowed_domains,
            prompt_variables=build_prompt_variables(request),
            output_dir=output_dir,
            warnings=warnings,
        )

    def artifacts(self) -> dict[str, str | None]:
        """Resolve the step artifact paths for this run (``None`` when disabled)."""
        return {key: self.artifact_path(filename) for key, filename in ARTIFACT_FILES.items()}

    def artifact_path(self, filename: str) -> str | None:
        """Return a step artifact path, creating the output directory on demand."""
        if self.output_dir is None:
            return None
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            logger.warning(
                "artifact_directory_unavailable",
                extra={"directory": str(self.output_dir)},
            )
            return None
        return str(self.output_dir / filename)


def build_prompt_variables(request: ProcurementRequest) -> dict[str, Any]:
    """Map the request onto the ``$variables`` used by the prompt files."""
    return {
        "product": request.product,
        "websites": ", ".join(request.websites),
        "country": request.country,
        "language": request.language,
        "max_queries": request.max_queries,
        "score_threshold": f"{request.score_threshold:.2f}",
        "top_n": request.top_n,
        "company_name": request.company_name,
    }


def parse_structured_output(
    raw_output: Any,
    model: type[StructuredModelT],
    *,
    step: str,
) -> StructuredModelT | None:
    """Best-effort extraction of a validated model from a CrewAI task output.

    CrewAI can hand back a pydantic instance, a dict or a JSON string depending on
    the model, the provider and whether conversion succeeded. Rather than trusting
    one shape we try all of them and record a warning when none works, so a single
    formatting slip cannot abort an otherwise successful run.
    """
    if raw_output is None:
        logger.warning("workflow_step_missing_output", extra={"step": step})
        return None

    if isinstance(raw_output, model):
        return raw_output

    candidate = getattr(raw_output, "pydantic", None)
    if isinstance(candidate, model):
        return candidate

    candidate = getattr(raw_output, "json_dict", None)
    if isinstance(candidate, BaseModel):
        candidate = candidate.model_dump()
    if isinstance(candidate, Mapping):
        try:
            return model.model_validate(dict(candidate))
        except Exception as error:  # noqa: BLE001 - fall through to the next shape
            logger.warning(
                "workflow_step_output_invalid",
                extra={"step": step, "shape": "json_dict", "error": type(error).__name__},
            )

    raw = getattr(raw_output, "raw", raw_output)
    if isinstance(raw, str) and raw.strip():
        try:
            return model.model_validate_json(_extract_json_object(raw))
        except Exception as error:  # noqa: BLE001 - reported below
            logger.warning(
                "workflow_step_output_invalid",
                extra={"step": step, "shape": "raw", "error": type(error).__name__},
            )

    logger.warning(
        "workflow_step_output_unusable",
        extra={"step": step, "shape": type(raw_output).__name__},
    )
    return None


def _extract_json_object(text: str) -> str:
    """Return the outermost JSON object from a possibly fenced/annotated string."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return text
    candidate = text[start : end + 1]
    try:
        json.loads(candidate)
    except json.JSONDecodeError:
        return text
    return candidate
