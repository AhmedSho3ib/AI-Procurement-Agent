#!/usr/bin/env python
"""End-to-end smoke test with mock providers.

Requires **no API keys and no network access**. It exercises the real container,
service, rules, report renderer and persistence layer, which makes it a useful
pre-deploy check and a CI gate.

Usage::

    python scripts/smoke_test.py
    python scripts/smoke_test.py --output-dir ai-agent-output --workflow sequential
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow ``python scripts/smoke_test.py`` from a fresh checkout.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.container import build_container
from src.schemas.procurement import ProcurementRequest, ProcurementStatus
from src.schemas.reports import ReportFormat
from src.utils.config import Settings
from src.utils.exceptions import AppError
from src.utils.logging import configure_logging


def build_request(args: argparse.Namespace) -> ProcurementRequest:
    return ProcurementRequest(
        product=args.product,
        websites=args.websites,
        max_queries=args.max_queries,
        top_n=args.top_n,
        report_format=ReportFormat(args.format),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run an offline end-to-end procurement smoke test.")
    parser.add_argument("--product", default="coffee machine for the office")
    parser.add_argument(
        "--websites",
        nargs="+",
        default=["www.amazon.eg", "www.jumia.com.eg", "www.noon.com/egypt-en"],
    )
    parser.add_argument("--max-queries", type=int, default=3)
    parser.add_argument("--top-n", type=int, default=3)
    parser.add_argument("--workflow", choices=["crew", "sequential"], default="sequential")
    parser.add_argument("--format", choices=[f.value for f in ReportFormat], default="html")
    parser.add_argument("--output-dir", default="", help="Directory for generated artifacts (optional)")
    args = parser.parse_args(argv)

    settings = Settings(
        _env_file=None,
        environment="test",
        log_level="INFO",
        log_format="console",
        llm_provider="mock",
        search_provider="mock",
        scraper_provider="mock",
        report_provider="jinja",
        workflow=args.workflow,
        persistence_backend="memory",
        report_output_dir=args.output_dir,
        agentops_enabled=False,
    )
    configure_logging(settings.log_level, settings.log_format)

    try:
        container = build_container(settings)
    except AppError as error:
        print(f"FAILED to build the container [{error.code}]: {error.message}", file=sys.stderr)
        return 1

    try:
        container.start()
        response = container.procurement_controller.create_procurement(build_request(args))

        report_path: Path | None = None
        if args.output_dir and response.report is not None:
            suffix = {"html": "html", "markdown": "md", "json": "json"}[response.report.format.value]
            report_path = Path(args.output_dir) / f"procurement_report.{suffix}"
            report_path.write_text(response.report.content, encoding="utf-8")

        print("\n--- smoke test summary -------------------------------------------------")
        print(f"status          : {response.status.value}")
        print(f"workflow        : {response.metadata.get('workflow')}")
        print(f"queries         : {len(response.queries)}")
        print(f"search results  : {len(response.search_results)}")
        print(f"products        : {len(response.products)}")
        if response.price_summary and response.price_summary.product_count:
            print(
                "price range     : "
                f"{response.price_summary.min_price} - {response.price_summary.max_price} "
                f"{response.price_summary.currency or ''}".rstrip()
            )
        print(f"report          : {'yes' if response.report else 'no'}")
        if report_path is not None:
            print(f"report file     : {report_path}")
        for warning in response.warnings:
            print(f"warning         : {warning}")

        failures: list[str] = []
        if response.status is not ProcurementStatus.COMPLETED:
            failures.append(f"expected status 'completed', got '{response.status.value}'")
        if not response.queries:
            failures.append("no search queries were generated")
        if not response.products:
            failures.append("no products were extracted")
        if response.report is None:
            failures.append("no report was rendered")
        elif not response.report.content.strip():
            failures.append("the rendered report is empty")

        if failures:
            print("\nSMOKE TEST FAILED:", file=sys.stderr)
            for failure in failures:
                print(f"  - {failure}", file=sys.stderr)
            return 1

        print("\nSMOKE TEST PASSED")
        return 0
    except AppError as error:
        print(f"SMOKE TEST FAILED [{error.code}]: {error.message}", file=sys.stderr)
        return 1
    finally:
        container.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
