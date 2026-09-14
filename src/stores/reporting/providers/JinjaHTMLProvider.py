"""Jinja2 report renderer.

Templates live in ``templates/`` next to the repository root, never inside Python
strings. HTML uses Bootstrap 5 exactly like the prototype, plus a small Chart.js
bar chart of the extracted prices.
"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, TemplateNotFound, select_autoescape

from src.schemas.reports import ProcurementReport, RenderedReport, ReportContext, ReportFormat
from src.stores.reporting.ReportEnums import ReportProvider
from src.stores.reporting.ReportInterface import ReportInterface
from src.utils.exceptions import ConfigurationError, ReportGenerationError
from src.utils.logging import get_logger

__all__ = ["JinjaHTMLProvider", "default_template_dir"]

logger = get_logger(__name__)

_TEMPLATE_BY_FORMAT: dict[ReportFormat, str] = {
    ReportFormat.HTML: "procurement_report.html",
    ReportFormat.MARKDOWN: "procurement_report.md.jinja",
}


def default_template_dir() -> Path:
    """Return the repository ``templates/`` directory.

    ``src/stores/reporting/providers/<this file>`` -> up four levels is the
    repository root. This keeps the Docker image and local runs identical.
    """
    return Path(__file__).resolve().parents[4] / "templates"


class JinjaHTMLProvider(ReportInterface):
    """Renders reports with Jinja2 templates loaded from disk."""

    provider = ReportProvider.JINJA

    def __init__(
        self,
        template_dir: Path | str | None = None,
        *,
        default_template: str | None = None,
    ) -> None:
        resolved = Path(template_dir) if template_dir else default_template_dir()
        if not resolved.is_dir():
            raise ConfigurationError(
                "Report template directory does not exist",
                context={"template_dir": str(resolved)},
            )
        self._template_dir = resolved
        self._environment = Environment(
            loader=FileSystemLoader(str(resolved)),
            autoescape=select_autoescape(enabled_extensions=("html", "xml")),
            trim_blocks=True,
            lstrip_blocks=True,
        )
        self._default_template = default_template or _TEMPLATE_BY_FORMAT[ReportFormat.HTML]
        self._environment.filters["money"] = _format_money

    def describe(self) -> dict[str, object]:
        return {
            "provider": self.provider.value,
            "template_dir": str(self._template_dir),
            "templates": sorted(path.name for path in self._template_dir.glob("*.html"))
            + sorted(path.name for path in self._template_dir.glob("*.jinja")),
        }

    def render(
        self,
        report: ProcurementReport,
        context: ReportContext,
        report_format: ReportFormat = ReportFormat.HTML,
    ) -> RenderedReport:
        if report_format is ReportFormat.JSON:
            payload = {
                "report": report.model_dump(mode="json"),
                "context": context.model_dump(mode="json"),
            }
            import json

            return RenderedReport(
                format=report_format,
                content=json.dumps(payload, indent=2, ensure_ascii=False),
                generated_at=context.generated_at,
            )

        template_name = self._template_name(report_format)
        try:
            template = self._environment.get_template(template_name)
            content = template.render(report=report, context=context)
        except TemplateNotFound as error:
            raise ConfigurationError(
                "Report template not found",
                context={"template": template_name, "template_dir": str(self._template_dir)},
                cause=error,
            ) from error
        except Exception as error:
            raise ReportGenerationError(
                "Report rendering failed",
                context={"template": template_name, "format": report_format.value},
                cause=error,
            ) from error

        return RenderedReport(format=report_format, content=content, generated_at=context.generated_at)

    def _template_name(self, report_format: ReportFormat) -> str:
        if report_format is ReportFormat.HTML:
            return self._default_template
        try:
            return _TEMPLATE_BY_FORMAT[report_format]
        except KeyError as error:
            raise ReportGenerationError(
                "Unsupported report format",
                context={"format": report_format.value, "supported": [f.value for f in _TEMPLATE_BY_FORMAT]},
                cause=error,
            ) from error


def _format_money(value: object) -> str:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "-"
    return f"{number:,.2f}"
