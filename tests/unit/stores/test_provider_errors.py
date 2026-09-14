"""Provider error classification, secret redaction and bounded retry behaviour.

This is the code that converts an opaque SDK failure (for example the prototype's
``Error code: 401 - unauthorized client``) into an actionable, retry-safe,
secret-free typed error.
"""

from __future__ import annotations

import pytest

from src.utils.exceptions import (
    AppError,
    ProviderAuthError,
    ProviderError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    classify_provider_error,
    is_retryable,
    redact_secrets,
)
from src.utils.retry import RetryPolicy, run_with_retry


class FakeHttpError(Exception):
    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class TestClassification:
    @pytest.mark.parametrize(
        "error",
        [
            FakeHttpError("Unauthorized", 401),
            FakeHttpError("Forbidden", 403),
            RuntimeError(
                "Error code: 401 - {'message': 'UNAUTHENTICATED', 'type': 'unauthorized_client_error'}"
            ),
            RuntimeError("API key not valid. Please pass a valid API key."),
            RuntimeError("UNAUTHENTICATED: unauthorized client detected"),
        ],
    )
    def test_credential_failures_are_permanent_auth_errors(self, error: Exception) -> None:
        classified = classify_provider_error(error, provider="gemini", operation="generate", context={})
        assert isinstance(classified, ProviderAuthError)
        assert classified.retryable is False
        assert classified.context["provider"] == "gemini"
        assert classified.context["operation"] == "generate"

    @pytest.mark.parametrize(
        "error",
        [
            FakeHttpError("Too many requests", 429),
            RuntimeError("RESOURCE_EXHAUSTED: quota exceeded"),
            RuntimeError("rate limit reached"),
        ],
    )
    def test_throttling_is_retryable(self, error: Exception) -> None:
        classified = classify_provider_error(error, provider="tavily", operation="search")
        assert isinstance(classified, ProviderRateLimitError)
        assert classified.retryable is True
        assert classified.http_status == 429

    @pytest.mark.parametrize("error", [TimeoutError("request timed out"), RuntimeError("deadline exceeded")])
    def test_timeouts_are_retryable(self, error: Exception) -> None:
        classified = classify_provider_error(error, provider="scrapegraph", operation="extract")
        assert isinstance(classified, ProviderTimeoutError)
        assert classified.retryable is True

    @pytest.mark.parametrize(
        "error",
        [
            FakeHttpError("Bad gateway", 502),
            FakeHttpError("Service unavailable", 503),
            RuntimeError("Connection reset by peer"),
        ],
    )
    def test_outages_are_retryable(self, error: Exception) -> None:
        classified = classify_provider_error(error, provider="tavily", operation="search")
        assert isinstance(classified, ProviderUnavailableError)
        assert classified.retryable is True

    def test_malformed_payloads_are_permanent(self) -> None:
        classified = classify_provider_error(
            ValueError("unexpected shape"), provider="tavily", operation="search"
        )
        assert isinstance(classified, ProviderResponseError)
        assert classified.retryable is False

    def test_already_classified_errors_pass_through(self) -> None:
        existing = ProviderAuthError("already typed", provider="gemini")
        assert classify_provider_error(existing, provider="gemini", operation="x") is existing

    def test_status_code_is_preserved_in_context(self) -> None:
        classified = classify_provider_error(FakeHttpError("nope", 429), provider="x", operation="y")
        assert classified.context["status_code"] == 429

    def test_all_classified_errors_are_app_errors(self) -> None:
        for error in (RuntimeError("502"), RuntimeError("timeout"), RuntimeError("unexpected")):
            assert isinstance(classify_provider_error(error, provider="p", operation="o"), AppError)


class TestSecretRedaction:
    @pytest.mark.parametrize(
        "secret",
        [
            "AIzaSyA1234567890abcdefghijklmnop",
            "AQ.Ab8RNoSecretValue123456",
            "sk-Ck1234567890abcdefghijkl",
            "tvly-abcdef1234567890abcdef",
            "sgai-abcdef1234567890abcdef",
        ],
    )
    def test_masks_credential_shaped_strings(self, secret: str) -> None:
        redacted = redact_secrets(f"provider said: {secret} is invalid")
        assert secret not in redacted
        assert "[REDACTED]" in redacted

    def test_masks_key_value_pairs(self) -> None:
        redacted = redact_secrets("api_key=abcdef1234567890abcdef")
        assert "abcdef1234567890abcdef" not in redacted

    def test_leaves_ordinary_text_untouched(self) -> None:
        assert redact_secrets("plain message about coffee machines") == "plain message about coffee machines"

    def test_classification_redacts_the_error_message(self) -> None:
        classified = classify_provider_error(
            RuntimeError("Error code: 401 - key sk-Ck1234567890abcdefghijkl rejected"),
            provider="agentrouter",
            operation="generate",
        )
        assert "sk-Ck1234567890abcdefghijkl" not in classified.message


class TestRetryPolicy:
    def test_does_not_retry_auth_failures(self) -> None:
        policy = RetryPolicy(attempts=3)
        assert policy.should_retry(ProviderAuthError("nope", provider="p")) is False

    def test_retries_transient_failures(self) -> None:
        policy = RetryPolicy(attempts=3)
        for error in (
            ProviderRateLimitError("slow down", provider="p"),
            ProviderTimeoutError("timeout", provider="p"),
            ProviderUnavailableError("down", provider="p"),
        ):
            assert policy.should_retry(error) is True

    def test_does_not_retry_validation_errors(self) -> None:
        assert RetryPolicy(attempts=3).should_retry(ValueError("bad input")) is False

    def test_never_retries_more_than_configured(self) -> None:
        assert RetryPolicy(attempts=0).attempts == 1
        assert RetryPolicy(attempts=10).attempts == 10

    def test_run_with_retry_stops_after_attempts(self) -> None:
        calls: list[int] = []
        delays: list[float] = []

        def operation() -> str:
            calls.append(1)
            raise ProviderRateLimitError("throttled", provider="p")

        with pytest.raises(ProviderRateLimitError):
            run_with_retry(
                operation, RetryPolicy(attempts=3, backoff_seconds=0.5), description="op", sleep=delays.append
            )

        assert len(calls) == 3
        assert delays == [0.5, 1.0]

    def test_run_with_retry_succeeds_after_a_transient_failure(self) -> None:
        attempts: list[int] = []

        def operation() -> str:
            attempts.append(1)
            if len(attempts) < 2:
                raise ProviderUnavailableError("down", provider="p")
            return "ok"

        result = run_with_retry(operation, RetryPolicy(attempts=3), description="op", sleep=lambda _: None)
        assert result == "ok"
        assert len(attempts) == 2

    def test_permanent_error_is_not_retried(self) -> None:
        calls: list[int] = []

        def operation() -> str:
            calls.append(1)
            raise ProviderAuthError("nope", provider="p")

        with pytest.raises(ProviderAuthError):
            run_with_retry(operation, RetryPolicy(attempts=3), description="op", sleep=lambda _: None)
        assert len(calls) == 1

    def test_retryable_flag_helpers(self) -> None:
        assert is_retryable(ProviderTimeoutError("t", provider="p")) is True
        assert is_retryable(ProviderError("generic", provider="p")) is False
