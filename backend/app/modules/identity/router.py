from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.session import get_db
from app.modules.audit.service import write_audit_log
from app.modules.auth.models import User
from app.modules.privacy.service import PrivacyService

router = APIRouter()


@router.get("/me")
def read_me(user: User = Depends(get_current_user)):
    """
    The authenticated learner. Identity comes from the trusted BFF headers,
    never from anything the browser supplied directly.
    """
    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "is_active": user.is_active,
        "tracking_consent": user.tracking_consent,
    }


class ConsentUpdate(BaseModel):
    tracking_consent: str = Field(pattern="^(full|minimal)$")


@router.patch("/me/consent")
def update_consent(
    body: ConsentUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Separated from core account settings on purpose: this is the one field
    that gates behavioral telemetry (events/router.py), not anything the
    core learning loop depends on -- declining it never breaks upload,
    generation, study, assessment, or recommendation.
    """
    user.tracking_consent = body.tracking_consent
    db.commit()
    return {"tracking_consent": user.tracking_consent}


@router.delete("/me", status_code=202)
def delete_my_account(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Cascades to every course, document, chunk, curriculum/mastery/
    adaptation/tutor record, and legacy record this user owns, then the
    user row itself -- see privacy/service.py's PrivacyService for exactly
    what that covers and what it deliberately does not touch (audit logs).
    Deletion is immediate and synchronous (no background-job
    infrastructure exists to defer it), which is a stricter guarantee than
    a scheduled one, not a weaker one.
    """
    write_audit_log(
        db, actor_user_id=user.id, action="account_deletion_requested",
        target_type="user", target_id=user.id,
    )
    PrivacyService(db).delete_account(user.id)
    return {"status": "deleted"}
