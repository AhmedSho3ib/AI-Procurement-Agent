"""CrewAI task factories (one module per agent/task pair)."""

from src.services.procurement_service.tasks.extraction_tasks import build_extraction_task
from src.services.procurement_service.tasks.query_tasks import build_query_task
from src.services.procurement_service.tasks.report_tasks import build_report_task
from src.services.procurement_service.tasks.search_tasks import build_search_task

__all__ = [
    "build_extraction_task",
    "build_query_task",
    "build_report_task",
    "build_search_task",
]
