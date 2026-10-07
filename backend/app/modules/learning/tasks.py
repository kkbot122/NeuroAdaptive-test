"""Celery entrypoint for durable fixed-assessment grading."""
from uuid import UUID

import app.db.model_registry  # noqa: F401
from app.core.celery_app import celery_app
from app.db.session import SessionLocal
from app.modules.learning.service import LearningService
from app.services.providers import embedding_gateway, generation_gateway


@celery_app.task(name="neurolearn.learning.grade_assessment_answer", bind=True)
def run_assessment_grading(self, answer_submission_id: str, owner_id: int) -> None:
    db = SessionLocal()
    try:
        LearningService(db, generation_gateway(), embedding_gateway()).grade_saved_answer(
            UUID(answer_submission_id), owner_id
        )
    finally:
        db.close()
