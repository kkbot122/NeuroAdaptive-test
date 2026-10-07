"""The sole API-to-worker dispatch seam for processing jobs."""
from typing import Protocol
from uuid import UUID

from app.modules.jobs.tasks import run_processing_job
from app.modules.preparation.dispatch import CeleryPreparationDispatcher, PreparationDispatcher


class JobDispatcher(Protocol):
    def enqueue(self, job_id: UUID, owner_id: int) -> None: ...


class CeleryJobDispatcher:
    """Enqueue only: all work occurs in the Celery worker process."""

    def enqueue(self, job_id: UUID, owner_id: int) -> None:
        run_processing_job.delay(str(job_id), owner_id)


__all__ = ["CeleryJobDispatcher", "CeleryPreparationDispatcher", "JobDispatcher", "PreparationDispatcher"]
