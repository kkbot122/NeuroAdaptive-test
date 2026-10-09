"""Worker entrypoints. Each task owns a fresh database session."""
from uuid import UUID

# Workers do not import FastAPI's application module, so register every model
# explicitly before JobService opens a session and resolves relationships.
import app.db.model_registry  # noqa: F401
from app.core.celery_app import celery_app
from app.db.session import SessionLocal
from app.modules.jobs.service import JobService
from app.services.ai_usage import ai_usage_scope


@celery_app.task(name="neurolearn.processing.run", bind=True)
def run_processing_job(self, job_id: str, owner_id: int) -> None:
    """Resume one durable processing job; duplicate deliveries are safe."""
    db = SessionLocal()
    try:
        with ai_usage_scope(db, owner_id, "processing", UUID(job_id), worker=True):
            JobService(db).run(UUID(job_id), owner_id)
    finally:
        db.close()
