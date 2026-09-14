"""Application-wide exception hierarchy.

Every failure that can cross a layer boundary is expressed as an :class:`AppError`
subclass so that the HTTP layer can translate it into a stable status code and the
service layer can reason about *retryability* instead of catching ``Exception``.

The hierarchy intentionally separates:

* configuration problems (programmer/operator error, never retried),
* invalid caller input (never retried),
* transient external failures (bounded retries),
* permanent external failures (never retried),
* internal workflow failures (reported, logged with context).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

__all__ = [
    "AppError",
    "ConfigurationError",
    "ExternalServiceError",
    "InvalidInputError",
    "NotFoundError",
    "PersistenceError",
    "ProcurementError",
    "ProviderAuthError",
    "ProviderError",
    "ProviderRateLimitError",
    "ProviderResponseError",
    "ProviderTimeoutError",
    "ProviderUnavailableError",
    "QueryGenerationError",
    "ReportGenerationError",
    "ScrapingError",
    "SearchProviderError",
    "classify_provider_error",
    "is_retryable",
    "redact_secrets",
]


class AppError(Exception):
    """Base class for all deliberately raised application errors."""

    #: Default HTTP status code used by the API error handler.
    http_status: int = 500
    #: Stable machine readable error code exposed through the API.
    code: str = "internal_error"
    #: Whether the operation can safely be retried without changing the input.
    retryable: bool = False

    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        context: Mapping[str, Any] | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        merged: dict[str, Any] = dict(context or {})
        if provider:
            # ``provider`` is a first-class, optional attribute so that any error
            # can name the external system involved without repeating it in every
            # context dict.
            merged.setdefault("provider", provider)
        self.context = merged
        if cause is not None:
            self.__cause__ = cause

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable, secret-free representation."""
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.context:
            payload["details"] = self.context
        return payload

    def __str__(self) -> str:  # pragma: no cover - trivial
        if not self.context:
            return self.message
        rendered = ", ".join(f"{key}={value!r}" for key, value in self.context.items())
        return f"{self.message} ({rendered})"


class ConfigurationError(AppError):
    """Missing or invalid configuration (env vars, provider wiring, templates)."""

    http_status = 500
    code = "configuration_error"


class InvalidInputError(AppError):
    """Caller supplied input that is syntactically valid but semantically unusable."""

    http_status = 422
    code = "invalid_input"


class NotFoundError(AppError):
    """A requested resource does not exist."""

    http_status = 404
    code = "not_found"


class ProcurementError(AppError):
    """Base class for failures inside the procurement workflow."""

    http_status = 502
    code = "procurement_error"


class QueryGenerationError(ProcurementError):
    """The LLM could not produce usable search queries."""

    code = "query_generation_failed"


class SearchProviderError(ProcurementError):
    """The search provider failed for a query (or all queries)."""

    code = "search_provider_failed"


class ScrapingError(ProcurementError):
    """A product page could not be scraped or the payload was unusable."""

    code = "scraping_failed"


class ReportGenerationError(ProcurementError):
    """Report content could not be produced or rendered."""

    code = "report_generation_failed"


class PersistenceError(AppError):
    """Persistence backend failure. Never leaks driver details to callers."""

    http_status = 500
    code = "persistence_error"


class ExternalServiceError(AppError):
    """Base class for third-party provider failures."""

    http_status = 502
    code = "external_service_error"


class ProviderError(ExternalServiceError):
    """Generic provider failure."""

    code = "provider_error"


class ProviderAuthError(ProviderError):
    """Credentials were rejected. Permanent: retrying cannot help."""

    http_status = 502
    code = "provider_auth_error"
    retryable = False


class ProviderRateLimitError(ProviderError):
    """Provider throttled the request. Transient."""

    http_status = 429
    code = "provider_rate_limited"
    retryable = True


class ProviderTimeoutError(ProviderError):
    """Provider did not answer in time. Transient."""

    http_status = 504
    code = "provider_timeout"
    retryable = True


class ProviderUnavailableError(ProviderError):
    """Provider is temporarily unreachable/overloaded. Transient."""

    http_status = 503
    code = "provider_unavailable"
    retryable = True


class ProviderResponseError(ProviderError):
    """Provider answered but the payload was malformed. Permanent."""

    code = "provider_response_invalid"
    retryable = False


def is_retryable(error: BaseException) -> bool:
    """Return ``True`` when a bounded retry may succeed for ``error``."""
    return isinstance(error, AppError) and error.retryable


# --------------------------------------------------------------------------- #
# Provider error classification + log sanitisation
# --------------------------------------------------------------------------- #

#: Credential shapes that must never reach a log line or an API response.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"AIza[0-9A-Za-z_\-]{10,}"),
    re.compile(r"AQ\.[0-9A-Za-z_\-\.]{10,}"),
    re.compile(r"sk-[0-9A-Za-z_\-]{10,}"),
    re.compile(r"tvly-[0-9A-Za-z_\-]{10,}"),
    re.compile(r"sgai-[0-9A-Za-z_\-]{10,}"),
    re.compile(r"(?i)(api[_-]?key|token|authorization)[\"'\s:=]+[0-9A-Za-z_\-\.]{12,}"),
)

_AUTH_MARKERS = (
    "401",
    "403",
    "unauthenticated",
    "unauthorized",
    "unauthorized_client_error",
    "invalid api key",
    "api key not valid",
    "permission denied",
    "authentication",
)
_RATE_LIMIT_MARKERS = ("429", "rate limit", "ratelimit", "quota", "resource_exhausted", "too many requests")
_TIMEOUT_MARKERS = ("timeout", "timed out", "time out", "deadline exceeded")
_UNAVAILABLE_MARKERS = (
    "500",
    "502",
    "503",
    "504",
    "connection",
    "unavailable",
    "temporarily",
    "overloaded",
    "service_unavailable",
)


def redact_secrets(value: object) -> str:
    """Return ``value`` as a string with anything resembling a credential masked."""
    text = str(value)
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


def classify_provider_error(
    error: BaseException,
    *,
    provider: str,
    operation: str,
    context: Mapping[str, Any] | None = None,
) -> ExternalServiceError:
    """Translate an SDK/HTTP exception into a typed, secret-free provider error.

    The classification drives retry behaviour: authentication and malformed
    responses are permanent (never retried), while rate limits, timeouts and
    outages are transient (bounded retries by the stores layer).

    This is what turns the prototype's opaque
    ``Error code: 401 - unauthorized client`` failure into an actionable,
    retry-safe, log-safe signal.
    """
    if isinstance(error, ExternalServiceError):
        return error

    status = _extract_status_code(error)
    message = redact_secrets(error)
    haystack = f"{status or ''} {message}".lower()
    merged_context: dict[str, Any] = {"operation": operation}
    if context:
        merged_context.update(context)
    if status is not None:
        merged_context["status_code"] = status

    if status in (401, 403) or any(marker in haystack for marker in _AUTH_MARKERS):
        return ProviderAuthError(
            f"{provider} rejected the credentials during {operation}",
            provider=provider,
            context=merged_context,
            cause=error,
        )
    if status == 429 or any(marker in haystack for marker in _RATE_LIMIT_MARKERS):
        return ProviderRateLimitError(
            f"{provider} throttled the request during {operation}",
            provider=provider,
            context=merged_context,
            cause=error,
        )
    if any(marker in haystack for marker in _TIMEOUT_MARKERS):
        return ProviderTimeoutError(
            f"{provider} timed out during {operation}",
            provider=provider,
            context=merged_context,
            cause=error,
        )
    if (status is not None and status >= 500) or any(marker in haystack for marker in _UNAVAILABLE_MARKERS):
        return ProviderUnavailableError(
            f"{provider} is unavailable during {operation}",
            provider=provider,
            context=merged_context,
            cause=error,
        )

    return ProviderResponseError(
        f"{provider} returned an unusable response during {operation}",
        provider=provider,
        context=merged_context,
        cause=error,
    )


def _extract_status_code(error: BaseException) -> int | None:
    """Best-effort status code extraction across SDKs (openai, httpx, google)."""
    for attribute in ("status_code", "status", "http_status", "code"):
        candidate = getattr(error, attribute, None)
        if isinstance(candidate, int) and 100 <= candidate <= 599:
            return candidate
    response = getattr(error, "response", None)
    candidate = getattr(response, "status_code", None)
    if isinstance(candidate, int) and 100 <= candidate <= 599:
        return candidate
    match = re.search(r"\b(4\d{2}|5\d{2})\b", str(error))
    return int(match.group(1)) if match else None
