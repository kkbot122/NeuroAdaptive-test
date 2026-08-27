from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.session import get_db
from app.modules.auth.models import User

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
    }
