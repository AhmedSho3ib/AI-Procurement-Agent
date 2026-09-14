"""Persistence layer: storage engines and data access.

Only the service layer talks to a repository, and it does so through the
``ProcurementRepository`` contract. No SQL, connection or driver detail escapes
this package.
"""

from src.persistence.models.procurement import (
    ProcurementRecord,
    ProcurementRepository,
    build_repository,
)

__all__ = ["ProcurementRecord", "ProcurementRepository", "build_repository"]
