#!/usr/bin/env python
"""Provider credential and configuration doctor.

Exists because of a real failure: the prototype's logs were full of

    Error in span Search Queries Recommendation Agent.agent: Error code: 401 -
    {'error': {'message': 'unauthorized client detected, contact support ...'},
     'message': 'UNAUTHENTICATED', 'success': False,
     'type': 'unauthorized_client_error'}

Investigation findings encoded in this script:

* The AgentOps key itself was **valid** (``/v3/auth/token`` returns HTTP 200), so
  the 401 was not an AgentOps API-key problem.
* The error body is an OpenAI-SDK style ``Error code: <status> - <json>`` message
  raised *inside an agent span*, i.e. an upstream LLM/relay endpoint rejected the
  credential - not application code and not the telemetry exporter.
* The prototype also enabled CrewAI's own OTLP exporter (``CREWAI_TRACING_ENABLED``)
  while AgentOps was managed manually, which duplicated spans and errors.

This script reports, for every configured provider, whether it is usable, and with
``--network`` performs live credential checks (nothing is ever printed except
status information - secrets stay redacted).

Usage::

    python scripts/check_providers.py                 # offline configuration report
    python scripts/check_providers.py --network       # also verify credentials live
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils.config import Settings
from src.utils.exceptions import redact_secrets

AGENTOPS_AUTH_URL = "https://api.agentops.ai/v3/auth/token"

_UNAUTHORIZED_HINT = (
    "A 401 'unauthorized client detected' / 'unauthorized_client_error' body means the remote "
    "endpoint rejected the credential. It is a credential or endpoint problem, not an "
    "application bug: verify the key, confirm it belongs to the endpoint you are calling, and "
    "rotate it if it was ever written to a notebook or committed."
)


def _status(ok: bool, detail: str) -> str:
    return f"{'OK  ' if ok else 'FAIL'} {detail}"


def check_agentops(settings: Settings, *, network: bool) -> list[str]:
    lines: list[str] = []
    key = settings.agentops_api_key
    configured = key is not None and bool(key.get_secret_value().strip())

    lines.append(
        _status(
            configured or not settings.agentops_enabled,
            f"agentops credential configured={configured}, enabled={settings.agentops_enabled}",
        )
    )
    if settings.agentops_enabled and not configured:
        lines.append("     AGENTOPS_ENABLED=true requires AGENTOPS_API_KEY")
        return lines

    if not network or not configured:
        lines.append("     live check skipped (use --network to verify the credential)")
        return lines

    payload = json.dumps({"api_key": key.get_secret_value().strip()}).encode()  # type: ignore[union-attr]
    request = urllib.request.Request(
        AGENTOPS_AUTH_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = json.loads(response.read().decode())
        lines.append(
            _status(
                bool(body.get("token")),
                f"agentops auth HTTP {response.status}, plan={body.get('project_prem_status')}, "
                f"token issued={bool(body.get('token'))}",
            )
        )
        if not body.get("token"):
            lines.append(f"     {_UNAUTHORIZED_HINT}")
    except urllib.error.HTTPError as error:
        detail = redact_secrets(error.read().decode()[:200])
        lines.append(_status(False, f"agentops auth HTTP {error.code}: {detail}"))
        if error.code in (401, 403):
            lines.append(f"     {_UNAUTHORIZED_HINT}")
    except Exception as error:  # noqa: BLE001 - diagnostics must never crash
        lines.append(_status(False, f"agentops auth check failed: {type(error).__name__}"))
    return lines


def check_credential(name: str, value: Any, *, required: bool) -> list[str]:
    configured = value is not None and bool(value.get_secret_value().strip())
    ok = configured or not required
    detail = f"{name} credential configured={configured}" + (
        "" if required else " (not required by current selection)"
    )
    return [_status(ok, detail)]


def check_gemini(settings: Settings, *, network: bool) -> list[str]:
    lines = check_credential("google", settings.google_api_key, required=settings.llm_provider == "gemini")
    if not network or settings.llm_provider != "gemini" or settings.google_api_key is None:
        return lines

    try:
        from google import genai

        client = genai.Client(api_key=settings.google_api_key.get_secret_value())
        models = list(client.models.list())
        lines.append(_status(bool(models), f"gemini reachable, {len(models)} models visible"))
    except Exception as error:  # noqa: BLE001 - reported, never raised
        message = redact_secrets(str(error))[:200]
        lines.append(_status(False, f"gemini check failed: {type(error).__name__}: {message}"))
        if "401" in message or "403" in message or "UNAUTHENTICATED" in message.upper():
            lines.append(f"     {_UNAUTHORIZED_HINT}")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Diagnose provider configuration and credentials.")
    parser.add_argument("--network", action="store_true", help="Perform live credential checks")
    args = parser.parse_args(argv)

    try:
        settings = Settings()
    except Exception as error:  # noqa: BLE001 - configuration problems are the point of this script
        print(f"FAIL configuration could not be loaded: {redact_secrets(error)}", file=sys.stderr)
        return 2

    print("Configuration")
    for key, value in settings.safe_summary().items():
        print(f"  {key}: {value}")

    print("\nCredentials and connectivity")
    failures = 0

    print("  [llm]")
    for line in check_gemini(settings, network=args.network):
        print(f"    {line}")
        failures += line.startswith("FAIL")

    print("  [search]")
    for line in check_credential(
        "tavily", settings.tvly_api_key, required=settings.search_provider == "tavily"
    ):
        print(f"    {line}")
        failures += line.startswith("FAIL")

    print("  [scraping]")
    for line in check_credential(
        "scrapegraph", settings.scrapegraph_api_key, required=settings.scraper_provider == "scrapegraph"
    ):
        print(f"    {line}")
        failures += line.startswith("FAIL")

    print("  [observability]")
    for line in check_agentops(settings, network=args.network):
        print(f"    {line}")
        failures += line.startswith("FAIL")

    print("\nNotes")
    print("  - Provider credentials are either present or absent; values are never printed.")
    print("  - AgentOps telemetry is optional and fail-safe: a telemetry outage never fails a request.")
    print(f"  - {_UNAUTHORIZED_HINT}")

    if failures:
        print(f"\n{failures} check(s) failed.", file=sys.stderr)
        return 1
    print("\nAll checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
