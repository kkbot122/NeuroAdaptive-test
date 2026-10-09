"""Celery entry points for recoverable private-object cleanup."""
from uuid import UUID

import app.db.model_registry  # noqa: F401
from app.core.celery_app import celery_app
from app.db.session import SessionLocal
from app.modules.documents.storage import StorageUnavailable
from app.modules.privacy.service import PrivacyService


@celery_app.task(
    name="neurolearn.privacy.cleanup_storage_object",
    bind=True,
    autoretry_for=(StorageUnavailable,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 5},
)
def cleanup_storage_object(self, task_id: str) -> None:
    db = SessionLocal()
    try:
        PrivacyService(db).run_storage_cleanup(UUID(task_id))
    finally:
        db.close()


@celery_app.task(name="neurolearn.privacy.expire_upload_intent")
def expire_upload_intent(intent_id: str) -> None:
    """Remove an abandoned, expired upload unless it was finalized."""
    from datetime import datetime, timezone

    from app.modules.documents.models import StorageUploadIntent

    db = SessionLocal()
    try:
        intent = db.query(StorageUploadIntent).filter(
            StorageUploadIntent.id == UUID(intent_id),
            StorageUploadIntent.finalized.is_(False),
            StorageUploadIntent.expires_at <= datetime.now(timezone.utc),
        ).with_for_update().first()
        if intent is None:
            return
        service = PrivacyService(db)
        task = service.schedule_storage_cleanup(intent.object_key, "private", "orphaned_upload", intent.owner_id)
        service._cleanup_task_ids.append(task.id)
        db.delete(intent)
        db.commit()
        service.dispatch_cleanup_tasks()
    finally:
        db.close()
