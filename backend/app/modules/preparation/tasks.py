"""Preparation task entrypoint; each delivery gets a fresh DB session."""
from uuid import UUID

import app.db.model_registry  # noqa: F401
from app.core.celery_app import celery_app
from app.db.session import SessionLocal
from app.modules.preparation.dispatch import CeleryPreparationDispatcher
from app.modules.preparation.service import ActivityPreparationService
from app.services.providers import generation_gateway


@celery_app.task(name="neurolearn.preparation.run", bind=True)
def run_activity_preparation(self, preparation_id: str, owner_id: int) -> None:
    db = SessionLocal()
    try:
        ActivityPreparationService(db, generation_gateway(), CeleryPreparationDispatcher()).run(
            UUID(preparation_id), owner_id
        )
    finally:
        db.close()
