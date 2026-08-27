from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.modules.auth.models import User


async def verify_internal_api_key(x_internal_token: str = Header(...)) -> None:
    """Reject any request that does not carry the shared BFF token."""
    if x_internal_token != settings.INTERNAL_API_KEY:
        raise HTTPException(status_code=403, detail="Could not validate credentials")


async def get_current_user(
    x_user_email: str = Header(...),
    x_internal_token: str = Header(...),
    db: Session = Depends(get_db),
) -> User:
    """
    Resolve the caller from the trusted BFF headers.

    The Next.js server validates the session and sets x-user-email; the shared
    internal token proves the request came from that server and not the browser.
    Never accept a user identifier from a query string or request body.
    """
    await verify_internal_api_key(x_internal_token)
    user = db.query(User).filter(User.email == x_user_email).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user
