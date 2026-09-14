"""Command line interface.

Runs exactly the same container, controller and service as the HTTP API - the CLI
only replaces the transport. Use it to script procurement runs, generate reports
locally, or smoke-test a provider configuration without starting a server.

Examples::

    procure --product "coffee machine for the office" \\
            --websites "www.amazon.eg,www.jumia.com.eg,www.noon.com/egypt-en"

    procure --product "office chair" --workflow sequential \\
            --llm-provider mock --search-provider mock --scraper-provider mock \\
            --format html --output chair-report.html --quiet
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from src.container import AppContainer, build_container
from src.schemas.procurement import ProcurementRequest, ProcurementResponse
from src.schemas.reports import ReportFormat
from src.utils.config import Settings
from src.utils.exceptions import AppError
from src.utils.logging import configure_logging, get_logger

__all__ = ["build_parser", "main"]

logger = get_logger(__name__)

_DEFAULT_WEBSITES = "www.amazon.eg,www.jumia.com.eg,www.noon.com/egypt-en"


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        prog="procure",
        description="Run an AI procurement research workflow from the command line.",
    )
    parser.add_argument("--product", required=True, help="What the company wants to buy")
    parser.add_argument(
        "--websites",
        default=_DEFAULT_WEBSITES,
        help=f"Comma separated target websites/domains (default: {_DEFAULT_WEBSITES})",
    )
    parser.add_argument("--country", default="Egypt", help="Target country (default: Egypt)")
    parser.add_argument("--language", default="English", help="Language of the generated queries")
    parser.add_argument("--company-name", default="Rankyx", help="Company the report is prepared for")
    parser.add_argument("--max-queries", type=int, default=10, help="Maximum number of search queries")
    parser.add_argument("--score-threshold", type=float, default=0.10, help="Minimum search relevance score")
    parser.add_argument("--top-n", type=int, default=10, help="Number of products to keep")

    # Execution overrides (default to .env / environment configuration).
    parser.add_argument("--workflow", choices=["crew", "sequential"], default=None)
    parser.add_argument("--llm-provider", choices=["gemini", "mock"], default=None)
    parser.add_argument("--search-provider", choices=["tavily", "mock"], default=None)
    parser.add_argument("--scraper-provider", choices=["scrapegraph", "mock"], default=None)
    parser.add_argument("--report-provider", choices=["jinja", "mock"], default=None)

    parser.add_argument(
        "--format",
        dest="report_format",
        choices=[report_format.value for report_format in ReportFormat],
        default="html",
    )
    parser.add_argument("--output", help="Write the rendered report to this file")
    parser.add_argument("--no-report", action="store_true", help="Skip report generation entirely")
    parser.add_argument("--json", action="store_true", help="Print the full response as JSON")
    parser.add_argument("--quiet", action="store_true", help="Only print the outcome summary")
    parser.add_argument(
        "--persistence",
        choices=["sqlite", "memory"],
        default=None,
        help="Override the persistence backend (default: configuration)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint. Returns a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    settings = _settings_from_args(args)
    configure_logging(settings.log_level, settings.log_format)

    request = ProcurementRequest(
        product=args.product,
        websites=[site.strip() for site in args.websites.split(",") if site.strip()],
        country=args.country,
        language=args.language,
        company_name=args.company_name,
        max_queries=args.max_queries,
        score_threshold=args.score_threshold,
        top_n=args.top_n,
        report_format=ReportFormat(args.report_format),
        generate_report=not args.no_report,
    )

    container: AppContainer | None = None
    try:
        container = build_container(settings)
        container.start()
        response = container.procurement_controller.create_procurement(request)
    except AppError as error:
        print(f"error [{error.code}]: {error.message}", file=sys.stderr)
        if error.context:
            print(f"details: {error.context}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:  # pragma: no cover - interactive
        print("cancelled", file=sys.stderr)
        return 130
    finally:
        if container is not None:
            container.shutdown()

    _emit(response, args, settings)
    return 0


def _settings_from_args(args: argparse.Namespace) -> Settings:
    overrides: dict[str, Any] = {}
    if args.workflow:
        overrides["workflow"] = args.workflow
    if args.llm_provider:
        overrides["llm_provider"] = args.llm_provider
    if args.search_provider:
        overrides["search_provider"] = args.search_provider
    if args.scraper_provider:
        overrides["scraper_provider"] = args.scraper_provider
    if args.report_provider:
        overrides["report_provider"] = args.report_provider
    if args.persistence:
        overrides["persistence_backend"] = args.persistence
    if args.quiet:
        overrides.setdefault("log_level", "WARNING")
    return Settings(**overrides)


def _emit(response: ProcurementResponse, args: argparse.Namespace, settings: Settings) -> None:
    if args.output and response.report is not None:
        destination = Path(args.output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(response.report.content, encoding="utf-8")

    if args.json:
        print(response.model_dump_json(indent=2))
        return

    currency = response.price_summary.currency if response.price_summary else None
    lines = [
        f"request_id      : {response.request_id}",
        f"status          : {response.status.value}",
        f"workflow        : {response.metadata.get('workflow', settings.workflow)}",
        f"queries         : {len(response.queries)}",
        f"search results  : {len(response.search_results)}",
        f"products        : {len(response.products)}",
    ]
    if response.price_summary and response.price_summary.product_count:
        price_range = (
            f"{response.price_summary.min_price} - {response.price_summary.max_price} {currency or ''}"
        ).rstrip()
        lines.append(f"price range     : {price_range}")
    lines.append(f"duration        : {response.duration_ms} ms")
    lines.append(f"report          : {'yes' if response.report else 'no'} ({args.report_format})")
    if args.output:
        lines.append(f"report file     : {args.output}")
    if response.warnings:
        lines.append("warnings        :")
        lines.extend(f"  - {warning}" for warning in response.warnings)

    print("\n".join(lines))


if __name__ == "__main__":  # pragma: no cover - manual entrypoint
    raise SystemExit(main())
