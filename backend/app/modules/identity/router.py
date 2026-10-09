from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from typing import Literal
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


class ConsentOut(BaseModel):
    tracking_consent: Literal["full", "minimal"]


class SettingsUpdate(BaseModel):
    tracking_consent: Literal["full", "minimal"]
    default_presentation_format: Literal["detailed", "concise", "worked_example", "analogy"]
    tutor_panel_open: bool


class SettingsOut(SettingsUpdate):
    pass


class PreferencesResetOut(BaseModel):
    default_presentation_format: Literal["detailed", "concise", "worked_example", "analogy"]
    tutor_panel_open: bool
    presentation_affinity_rows_removed: int


class AccountDeletionOut(BaseModel):
    status: Literal["deleted", "cleanup_pending"]
    pending_storage_cleanups: int = Field(ge=0)


@router.patch("/me/consent", response_model=ConsentOut)
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
    consent = PrivacyService(db).update_tracking_consent(user.id, body.tracking_consent)
    return {"tracking_consent": consent}


@router.get("/me/settings", response_model=SettingsOut)
def read_settings(user: User = Depends(get_current_user)):
    return {
        "tracking_consent": user.tracking_consent,
        "default_presentation_format": user.default_presentation_format,
        "tutor_panel_open": user.tutor_panel_open,
    }


@router.patch("/me/settings", response_model=SettingsOut)
def update_settings(
    body: SettingsUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return PrivacyService(db).update_settings(
        user.id,
        tracking_consent=body.tracking_consent,
        default_presentation_format=body.default_presentation_format,
        tutor_panel_open=body.tutor_panel_open,
    )


@router.post("/me/preferences/reset", response_model=PreferencesResetOut)
def reset_preferences(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return PrivacyService(db).reset_presentation_preferences(user.id)


@router.delete("/me", status_code=202, response_model=AccountDeletionOut)
def delete_my_account(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Removes owned database records synchronously and records every private
    storage target in the durable cleanup queue before removing its owner or
    source row. The response reports pending file cleanup honestly.
    """
    write_audit_log(
        db, actor_user_id=user.id, action="account_deletion_requested",
        target_type="user", target_id=user.id,
    )
    pending = PrivacyService(db).delete_account(user.id)
    return {
        "status": "cleanup_pending" if pending else "deleted",
        "pending_storage_cleanups": pending,
    }
