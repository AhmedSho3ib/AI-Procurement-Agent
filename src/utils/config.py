"""Type-safe, centralised application configuration.

All environment access happens here. Nothing else in the codebase reads
``os.environ`` for application configuration, which keeps secrets in one place and
makes the application testable by constructing :class:`Settings` directly.

Two deliberate design decisions
-------------------------------
1. **Namespaced behaviour settings.** Application settings use the ``APP_``
   prefix (``APP_PORT``, ``APP_DEBUG``, ``APP_ENVIRONMENT``). Ambient variables
   such as a stray ``DEBUG`` or ``PORT`` in a host environment must not silently
   reconfigure the service - a collision that this project hit in practice.
2. **Canonical provider credentials.** ``GOOGLE_API_KEY``, ``TVLY_API_KEY``,
   ``SCRAPEGRAPH_API_KEY`` and friends keep their canonical, unprefixed names so
   that existing provider tooling and the original ``.env`` keep working.

Secrets use :class:`pydantic.SecretStr`, so accidental logging or serialisation
renders ``**********`` instead of the credential.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = [
    "Environment",
    "LLMProviderName",
    "LogFormat",
    "PersistenceBackend",
    "ReportProviderName",
    "ScraperProviderName",
    "SearchProviderName",
    "Settings",
    "WorkflowName",
    "get_settings",
    "reset_settings_cache",
]

Environment = Literal["local", "development", "staging", "production", "test"]
LogFormat = Literal["console", "json"]

# Provider names intentionally mirror the enums in ``src.stores.*``. They are
# declared as literals here so that ``utils`` never imports ``stores`` (which
# would invert the dependency direction); a test asserts the two stay in sync.
LLMProviderName = Literal["gemini", "mock"]
SearchProviderName = Literal["tavily", "mock"]
ScraperProviderName = Literal["scrapegraph", "mock"]
ReportProviderName = Literal["jinja", "mock"]
WorkflowName = Literal["crew", "sequential"]
PersistenceBackend = Literal["sqlite", "memory"]

_ENV_PREFIX = "APP_"


class Settings(BaseSettings):
    """Runtime configuration resolved from ``APP_*`` variables and provider keys."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix=_ENV_PREFIX,
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    # --- Application -------------------------------------------------------
    app_name: str = "AI Procurement Agent"
    app_version: str = "1.0.0"
    environment: Environment = "local"
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = Field(default=8000, ge=1, le=65535)
    api_prefix: str = "/api/v1"
    cors_allow_origins: str = ""  # comma separated; empty disables CORS entirely

    # --- Logging -----------------------------------------------------------
    log_level: str = "INFO"
    log_format: LogFormat = "console"

    # --- Provider selection -------------------------------------------------
    llm_provider: LLMProviderName = "gemini"
    search_provider: SearchProviderName = "tavily"
    scraper_provider: ScraperProviderName = "scrapegraph"
    report_provider: ReportProviderName = "jinja"
    workflow: WorkflowName = "crew"

    # --- LLM (canonical provider credentials) -------------------------------
    google_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("GOOGLE_API_KEY", "APP_GOOGLE_API_KEY"),
    )
    google_model: str = Field(
        default="gemini-2.0-flash",
        validation_alias=AliasChoices("GOOGLE_MODEL", "APP_GOOGLE_MODEL"),
    )
    llm_temperature: float = Field(default=0.0, ge=0.0, le=2.0)

    # --- Search -------------------------------------------------------------
    tvly_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("TVLY_API_KEY", "TAVILY_API_KEY", "APP_TVLY_API_KEY"),
    )
    search_max_results: int = Field(default=10, ge=1, le=50)

    # --- Scraping -----------------------------------------------------------
    scrapegraph_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("SCRAPEGRAPH_API_KEY", "APP_SCRAPEGRAPH_API_KEY"),
    )

    # --- Observability ------------------------------------------------------
    agentops_enabled: bool = False
    agentops_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("AGENTOPS_API_KEY", "APP_AGENTOPS_API_KEY"),
    )
    agentops_skip_auto_end_session: bool = True
    agentops_default_tags: str = "crewai,procurement"
    crewai_tracing_enabled: bool = False

    # --- Reliability --------------------------------------------------------
    request_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    max_retries: int = Field(default=3, ge=1, le=10)
    retry_backoff_seconds: float = Field(default=0.5, ge=0, le=30)

    # --- Persistence --------------------------------------------------------
    persistence_backend: PersistenceBackend = "sqlite"
    sqlite_path: str = "data/procurement.db"

    # --- Reporting ----------------------------------------------------------
    report_output_dir: str = "ai-agent-output"
    report_template_dir: str = "templates"
    report_template: str = "procurement_report.html"

    # ------------------------------------------------------------------ utils
    @field_validator("log_level")
    @classmethod
    def _uppercase_log_level(cls, value: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        normalised = value.strip().upper()
        if normalised not in allowed:
            raise ValueError(f"log_level must be one of {sorted(allowed)}")
        return normalised

    @model_validator(mode="after")
    def _validate_provider_credentials(self) -> Settings:
        """Fail fast on *internally inconsistent* configuration.

        Missing credentials for a selected real provider is operator error, so it
        is surfaced at startup rather than during the first request. Mock providers
        never require credentials.
        """
        if self.llm_provider == "gemini" and not self._has_secret(self.google_api_key):
            raise ValueError("LLM_PROVIDER=gemini requires GOOGLE_API_KEY to be set")
        if self.search_provider == "tavily" and not self._has_secret(self.tvly_api_key):
            raise ValueError("SEARCH_PROVIDER=tavily requires TVLY_API_KEY to be set")
        if self.scraper_provider == "scrapegraph" and not self._has_secret(self.scrapegraph_api_key):
            raise ValueError("SCRAPER_PROVIDER=scrapegraph requires SCRAPEGRAPH_API_KEY to be set")
        if self.agentops_enabled and not self._has_secret(self.agentops_api_key):
            raise ValueError("AGENTOPS_ENABLED=true requires AGENTOPS_API_KEY to be set")
        return self

    @staticmethod
    def _has_secret(value: SecretStr | None) -> bool:
        return value is not None and bool(value.get_secret_value().strip())

    # ------------------------------------------------------------- properties
    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def agentops_tags(self) -> list[str]:
        return [tag.strip() for tag in self.agentops_default_tags.split(",") if tag.strip()]

    @property
    def sqlite_file(self) -> Path:
        return Path(self.sqlite_path)

    @property
    def report_output_path(self) -> Path:
        return Path(self.report_output_dir)

    def safe_summary(self) -> dict[str, object]:
        """Configuration snapshot with **no** secret material.

        Safe to return from ``/health``, log at startup or attach to a trace.
        """
        return {
            "app_name": self.app_name,
            "app_version": self.app_version,
            "environment": self.environment,
            "debug": self.debug,
            "log_level": self.log_level,
            "log_format": self.log_format,
            "providers": {
                "llm": self.llm_provider,
                "search": self.search_provider,
                "scraper": self.scraper_provider,
                "report": self.report_provider,
            },
            "workflow": self.workflow,
            "model": self.google_model if self.llm_provider == "gemini" else self.llm_provider,
            "persistence_backend": self.persistence_backend,
            "agentops_enabled": self.agentops_enabled,
            "credentials_configured": {
                "google": self._has_secret(self.google_api_key),
                "tavily": self._has_secret(self.tvly_api_key),
                "scrapegraph": self._has_secret(self.scrapegraph_api_key),
                "agentops": self._has_secret(self.agentops_api_key),
            },
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()


def reset_settings_cache() -> None:
    """Clear the settings cache (used by tests and by CLI re-configuration)."""
    get_settings.cache_clear()
