from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db

router = APIRouter()


@router.get("/health")
def health():
    """Liveness only. Deliberately touches no dependency."""
    return {"status": "healthy", "service": settings.PROJECT_NAME}


@router.get("/health/db")
def health_db(db: Session = Depends(get_db)):
    """
    Readiness for the database specifically.

    Returns 503 rather than 200-with-an-error-field so that a probe treats a
    dead database as down. The exception text is not echoed to the caller —
    it can carry connection strings.
    """
    try:
        db.execute(text("SELECT 1"))
        return {"status": "healthy", "database": "reachable"}
    except Exception as exc:
        import logging

        logging.getLogger(__name__).error("Database health check failed: %s", type(exc).__name__)
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "database": "unreachable"},
        )
