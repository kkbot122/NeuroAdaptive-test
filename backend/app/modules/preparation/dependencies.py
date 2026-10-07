from fastapi import Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.preparation.dispatch import CeleryPreparationDispatcher
from app.modules.preparation.service import ActivityPreparationService
from app.services.providers import generation_gateway


def get_preparation_service(db: Session = Depends(get_db)) -> ActivityPreparationService:
    return ActivityPreparationService(db, generation_gateway(), CeleryPreparationDispatcher())
