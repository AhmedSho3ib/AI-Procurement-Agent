"""Procurement persistence: schema, records and repositories.

Scope is deliberately minimal. The product needs an auditable trail of
procurement runs (what was asked, what was found, which report was produced), so
a single table is enough. There is no ORM, no migrations framework and no
connection pool to operate: SQLite via the standard library covers the load and
keeps the deployment a single container.

Database concerns stay here. Routes, schemas and the service never see SQL.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.schemas.procurement import ProcurementResponse
from src.utils.exceptions import PersistenceError
from src.utils.logging import get_logger

__all__ = [
    "SCHEMA_STATEMENTS",
    "InMemoryProcurementRepository",
    "ProcurementRecord",
    "ProcurementRepository",
    "SQLiteProcurementRepository",
    "build_repository",
]

logger = get_logger(__name__)

#: DDL for the storage engine. Executed once at startup (idempotent).
SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS procurements (
        request_id      TEXT PRIMARY KEY,
        product         TEXT NOT NULL,
        country         TEXT NOT NULL,
        status          TEXT NOT NULL,
        product_count   INTEGER NOT NULL DEFAULT 0,
        report_format   TEXT,
        report_content  TEXT,
        response_json   TEXT NOT NULL,
        created_at      TEXT NOT NULL,
        duration_ms     INTEGER NOT NULL DEFAULT 0
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_procurements_created_at ON procurements (created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_procurements_product ON procurements (product)",
)


@dataclass(frozen=True, slots=True)
class ProcurementRecord:
    """A persisted procurement run."""

    request_id: str
    product: str
    country: str
    status: str
    product_count: int
    created_at: datetime
    duration_ms: int
    response: ProcurementResponse
    report_format: str | None = None
    report_content: str | None = None

    @classmethod
    def from_response(cls, response: ProcurementResponse) -> ProcurementRecord:
        report = response.report
        return cls(
            request_id=response.request_id,
            product=response.request.product,
            country=response.request.country,
            status=response.status.value,
            product_count=len(response.products),
            created_at=response.generated_at,
            duration_ms=response.duration_ms,
            response=response,
            report_format=report.format.value if report else None,
            report_content=report.content if report else None,
        )


class ProcurementRepository(ABC):
    """Persistence contract used by the service layer."""

    @abstractmethod
    def save(self, record: ProcurementRecord) -> None:
        """Insert or replace ``record``."""

    @abstractmethod
    def get(self, request_id: str) -> ProcurementRecord | None:
        """Return the stored record or ``None``."""

    @abstractmethod
    def list_recent(self, limit: int = 20) -> list[ProcurementRecord]:
        """Return the most recent records, newest first."""

    @abstractmethod
    def close(self) -> None:
        """Release any underlying resources."""


class InMemoryProcurementRepository(ProcurementRepository):
    """Process-local repository used by tests and ``PERSISTENCE_BACKEND=memory``."""

    def __init__(self, *, max_entries: int = 500) -> None:
        self._records: dict[str, ProcurementRecord] = {}
        self._max_entries = max_entries
        self._lock = threading.Lock()

    def save(self, record: ProcurementRecord) -> None:
        with self._lock:
            self._records[record.request_id] = record
            if len(self._records) > self._max_entries:
                oldest = sorted(self._records.values(), key=lambda item: item.created_at)[0]
                self._records.pop(oldest.request_id, None)

    def get(self, request_id: str) -> ProcurementRecord | None:
        with self._lock:
            return self._records.get(request_id)

    def list_recent(self, limit: int = 20) -> list[ProcurementRecord]:
        with self._lock:
            records = sorted(self._records.values(), key=lambda item: item.created_at, reverse=True)
        return records[: max(1, limit)]

    def close(self) -> None:  # pragma: no cover - nothing to release
        return None


class SQLiteProcurementRepository(ProcurementRepository):
    """SQLite backed repository (default for local runs and the Docker image)."""

    def __init__(self, database_path: Path | str, *, timeout_seconds: float = 10.0) -> None:
        self._path = Path(database_path)
        self._lock = threading.Lock()
        try:
            if str(self._path) != ":memory:":
                self._path.parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(
                str(self._path),
                timeout=timeout_seconds,
                check_same_thread=False,
            )
            self._connection.row_factory = sqlite3.Row
            with self._connection:
                for statement in SCHEMA_STATEMENTS:
                    self._connection.execute(statement)
        except sqlite3.Error as error:
            raise PersistenceError(
                "Could not initialise the procurement database",
                context={"path": str(self._path), "error": type(error).__name__},
                cause=error,
            ) from error
        logger.debug("procurement_repository_ready", extra={"backend": "sqlite", "path": str(self._path)})

    def save(self, record: ProcurementRecord) -> None:
        payload = record.response.model_dump_json()
        try:
            with self._lock, self._connection:
                self._connection.execute(
                    """
                    INSERT OR REPLACE INTO procurements (
                        request_id, product, country, status, product_count,
                        report_format, report_content, response_json, created_at, duration_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.request_id,
                        record.product,
                        record.country,
                        record.status,
                        record.product_count,
                        record.report_format,
                        record.report_content,
                        payload,
                        record.created_at.astimezone(UTC).isoformat(),
                        record.duration_ms,
                    ),
                )
        except sqlite3.Error as error:
            raise PersistenceError(
                "Could not persist the procurement run",
                context={"request_id": record.request_id, "error": type(error).__name__},
                cause=error,
            ) from error

    def get(self, request_id: str) -> ProcurementRecord | None:
        try:
            with self._lock:
                cursor = self._connection.execute(
                    "SELECT * FROM procurements WHERE request_id = ?",
                    (request_id,),
                )
                row = cursor.fetchone()
        except sqlite3.Error as error:
            raise PersistenceError(
                "Could not read the procurement run",
                context={"request_id": request_id, "error": type(error).__name__},
                cause=error,
            ) from error
        return _row_to_record(row) if row is not None else None

    def list_recent(self, limit: int = 20) -> list[ProcurementRecord]:
        try:
            with self._lock:
                cursor = self._connection.execute(
                    "SELECT * FROM procurements ORDER BY created_at DESC LIMIT ?",
                    (max(1, limit),),
                )
                rows = cursor.fetchall()
        except sqlite3.Error as error:
            raise PersistenceError(
                "Could not list procurement runs",
                context={"error": type(error).__name__},
                cause=error,
            ) from error
        return [record for row in rows if (record := _row_to_record(row)) is not None]

    def close(self) -> None:
        try:
            with self._lock:
                self._connection.close()
        except sqlite3.Error:  # pragma: no cover - closing is best effort
            logger.debug("procurement_repository_close_failed")


def _row_to_record(row: sqlite3.Row) -> ProcurementRecord | None:
    try:
        payload: dict[str, Any] = json.loads(row["response_json"])
        response = ProcurementResponse.model_validate(payload)
    except Exception as error:  # noqa: BLE001 - a corrupt row must not crash the API
        logger.error(
            "procurement_record_unreadable",
            extra={"request_id": row["request_id"], "error": type(error).__name__},
        )
        return None

    created_at = datetime.fromisoformat(row["created_at"]) if row["created_at"] else response.generated_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)

    return ProcurementRecord(
        request_id=row["request_id"],
        product=row["product"],
        country=row["country"],
        status=row["status"],
        product_count=row["product_count"],
        created_at=created_at,
        duration_ms=row["duration_ms"],
        response=response,
        report_format=row["report_format"],
        report_content=row["report_content"],
    )


def build_repository(settings: Any) -> ProcurementRepository:
    """Create the repository selected by configuration (composition-time choice)."""
    if settings.persistence_backend == "memory":
        return InMemoryProcurementRepository()
    return SQLiteProcurementRepository(settings.sqlite_path, timeout_seconds=settings.request_timeout_seconds)
