"""Persistence layer: SQLite and in-memory repositories behind one contract."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from src.persistence.models.procurement import (
    InMemoryProcurementRepository,
    ProcurementRecord,
    ProcurementRepository,
    SQLiteProcurementRepository,
    build_repository,
)
from src.schemas.procurement import (
    ProcurementRequest,
    ProcurementResponse,
    ProcurementStatus,
)
from src.schemas.reports import RenderedReport, ReportFormat
from src.utils.exceptions import PersistenceError
from tests.conftest import make_settings


def _response(
    request_id: str = "req-0001",
    product: str = "coffee machine",
    generated_at: datetime | None = None,
) -> ProcurementResponse:
    return ProcurementResponse(
        request_id=request_id,
        status=ProcurementStatus.COMPLETED,
        request=ProcurementRequest(product=product, websites=["www.amazon.eg"]),
        report=RenderedReport(format=ReportFormat.HTML, content="<html>report</html>"),
        duration_ms=42,
        generated_at=generated_at or datetime.now(UTC),
    )


@pytest.fixture(params=["sqlite", "memory"])
def repository(request, tmp_path: Path) -> ProcurementRepository:  # type: ignore[no-untyped-def]
    if request.param == "sqlite":
        repo: ProcurementRepository = SQLiteProcurementRepository(tmp_path / "procurement.db")
    else:
        repo = InMemoryProcurementRepository()
    yield repo
    repo.close()


class TestRepositoryContract:
    """Both implementations must satisfy the same contract."""

    def test_save_and_get_round_trip(self, repository: ProcurementRepository) -> None:
        response = _response()
        repository.save(ProcurementRecord.from_response(response))

        record = repository.get("req-0001")

        assert record is not None
        assert record.request_id == "req-0001"
        assert record.product == "coffee machine"
        assert record.status == ProcurementStatus.COMPLETED.value
        assert record.report_format == ReportFormat.HTML.value
        assert record.report_content == "<html>report</html>"
        assert record.response.request_id == "req-0001"
        assert record.response.request.product == "coffee machine"

    def test_get_unknown_id_returns_none(self, repository: ProcurementRepository) -> None:
        assert repository.get("does-not-exist") is None

    def test_saving_the_same_id_twice_upserts(self, repository: ProcurementRepository) -> None:
        repository.save(ProcurementRecord.from_response(_response(product="first")))
        repository.save(ProcurementRecord.from_response(_response(product="second")))

        record = repository.get("req-0001")
        assert record is not None
        assert record.product == "second"

    def test_list_recent_returns_newest_first(self, repository: ProcurementRepository) -> None:
        base = datetime(2026, 1, 1, tzinfo=UTC)
        for index in range(3):
            response = _response(
                request_id=f"req-{index}",
                product=f"product {index}",
                generated_at=base + timedelta(minutes=index),
            )
            repository.save(ProcurementRecord.from_response(response))

        records = repository.list_recent(limit=10)

        assert [record.request_id for record in records] == ["req-2", "req-1", "req-0"]

    def test_list_recent_honours_the_limit(self, repository: ProcurementRepository) -> None:
        for index in range(5):
            repository.save(ProcurementRecord.from_response(_response(request_id=f"req-{index}")))
        assert len(repository.list_recent(limit=2)) == 2


class TestSQLiteSpecifics:
    def test_creates_the_database_directory(self, tmp_path: Path) -> None:
        target = tmp_path / "nested" / "dir" / "procurement.db"
        repository = SQLiteProcurementRepository(target)
        repository.close()
        assert target.exists()

    def test_initialisation_failure_raises_persistence_error(self, tmp_path: Path) -> None:
        # A directory path used as a database file cannot be opened.
        directory = tmp_path / "a-directory"
        directory.mkdir()
        with pytest.raises(PersistenceError):
            SQLiteProcurementRepository(directory)

    def test_corrupt_rows_are_skipped_not_fatal(self, tmp_path: Path) -> None:
        path = tmp_path / "procurement.db"
        repository = SQLiteProcurementRepository(path)
        repository.save(ProcurementRecord.from_response(_response()))
        repository.close()

        with sqlite3.connect(path) as connection:
            connection.execute("UPDATE procurements SET response_json = 'not json'")

        reopened = SQLiteProcurementRepository(path)
        assert reopened.get("req-0001") is None
        assert reopened.list_recent() == []
        reopened.close()

    def test_records_are_queryable_by_product(self, tmp_path: Path) -> None:
        path = tmp_path / "procurement.db"
        repository = SQLiteProcurementRepository(path)
        repository.save(ProcurementRecord.from_response(_response(product="standing desk")))
        repository.close()

        with sqlite3.connect(path) as connection:
            rows = connection.execute(
                "SELECT product FROM procurements WHERE product = ?", ("standing desk",)
            ).fetchall()
        assert rows == [("standing desk",)]


class TestInMemorySpecifics:
    def test_evicts_the_oldest_entry_when_full(self) -> None:
        repository = InMemoryProcurementRepository(max_entries=2)
        for index in range(3):
            repository.save(ProcurementRecord.from_response(_response(request_id=f"req-{index}")))
        assert len(repository.list_recent(limit=10)) == 2


class TestRepositoryFactory:
    def test_selects_memory_backend(self) -> None:
        repository = build_repository(make_settings(persistence_backend="memory"))
        assert isinstance(repository, InMemoryProcurementRepository)

    def test_selects_sqlite_backend(self, tmp_path: Path) -> None:
        repository = build_repository(
            make_settings(persistence_backend="sqlite", sqlite_path=str(tmp_path / "db.sqlite"))
        )
        assert isinstance(repository, SQLiteProcurementRepository)
        repository.close()
