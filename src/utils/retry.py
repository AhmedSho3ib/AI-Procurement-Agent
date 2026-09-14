"""Retry helper with explicit, bounded policies.

Used by the stores layer only. The policy distinguishes transient provider
failures (retried) from permanent ones such as authentication problems, invalid
responses or validation errors (never retried).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TypeVar

from src.utils.exceptions import AppError, is_retryable
from src.utils.logging import get_logger

__all__ = ["TRANSIENT_EXCEPTIONS", "RetryPolicy", "run_with_retry"]

logger = get_logger(__name__)

T = TypeVar("T")

#: Network level exceptions that are always worth a bounded retry.
TRANSIENT_EXCEPTIONS: tuple[type[BaseException], ...]
try:  # pragma: no cover - httpx is a hard dependency, defensive nonetheless
    import httpx

    TRANSIENT_EXCEPTIONS = (httpx.TimeoutException, httpx.TransportError)
except Exception:  # noqa: BLE001 - optional import fallback, never fatal
    TRANSIENT_EXCEPTIONS = ()


class RetryPolicy:
    """Immutable retry configuration.

    ``attempts`` counts the initial call, so ``attempts=3`` means at most two
    retries. Retries are never infinite and validation/permanent errors are
    excluded by default.
    """

    __slots__ = ("attempts", "backoff_seconds", "retry_on")

    def __init__(
        self,
        attempts: int = 3,
        backoff_seconds: float = 0.5,
        retry_on: Iterable[type[BaseException]] | None = None,
    ) -> None:
        self.attempts = max(1, int(attempts))
        self.backoff_seconds = max(0.0, float(backoff_seconds))
        extra = tuple(retry_on) if retry_on else ()
        self.retry_on: tuple[type[BaseException], ...] = (*TRANSIENT_EXCEPTIONS, *extra)

    def should_retry(self, error: BaseException) -> bool:
        if isinstance(error, AppError):
            return is_retryable(error)
        return bool(self.retry_on) and isinstance(error, self.retry_on)

    def delay_for(self, attempt: int) -> float:
        """Linear backoff: 0.5s, 1.0s, 1.5s ... capped implicitly by attempt count."""
        return self.backoff_seconds * attempt


def run_with_retry(
    operation: Callable[[], T],
    policy: RetryPolicy,
    *,
    description: str,
    sleep: Callable[[float], None] | None = None,
) -> T:
    """Execute ``operation`` honouring ``policy``.

    Args:
        operation: Zero-argument callable performing the provider call.
        policy: Bounded retry configuration.
        description: Human readable label used in logs (never contains secrets).
        sleep: Injectable sleeper so tests do not need real delays.

    Raises:
        BaseException: The last error when it is permanent or retries are exhausted.
    """
    waiter = sleep or _default_sleep
    last_error: BaseException | None = None

    for attempt in range(1, policy.attempts + 1):
        try:
            return operation()
        except BaseException as error:
            last_error = error
            if not policy.should_retry(error):
                raise
            if attempt >= policy.attempts:
                logger.warning(
                    "retry_exhausted",
                    extra={"operation": description, "attempts": attempt, "error": type(error).__name__},
                )
                raise
            delay = policy.delay_for(attempt)
            logger.warning(
                "retrying_operation",
                extra={
                    "operation": description,
                    "attempt": attempt,
                    "next_attempt": attempt + 1,
                    "delay_seconds": delay,
                    "error": type(error).__name__,
                },
            )
            waiter(delay)

    # Unreachable: the loop either returns or raises.
    assert last_error is not None  # pragma: no cover - defensive
    raise last_error  # pragma: no cover - defensive


def _default_sleep(seconds: float) -> None:
    import time

    time.sleep(seconds)
