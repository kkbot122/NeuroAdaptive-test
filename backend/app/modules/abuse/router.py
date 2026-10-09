"""Read-only usage for the authenticated owner, never caller-supplied identity."""
from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.session import get_db
from app.modules.auth.models import User
from app.modules.abuse.models import AIProviderCall, AIUsageDaily
from app.modules.abuse.schemas import AIUsageOut
from app.modules.abuse.service import AbuseControlService, DAILY_AI_CALL_BUDGET
from app.modules.abuse.usage import usage_totals

router = APIRouter()


@router.get("/ai/usage", response_model=AIUsageOut)
def get_usage(response: Response, usage_date: date | None = None, limit: int = Query(default=25, ge=1, le=100),
              user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    day = usage_date or datetime.now(timezone.utc).date()
    start = datetime.combine(day, time.min, timezone.utc)
    query = db.query(AIProviderCall).filter(AIProviderCall.owner_id == user.id,
        AIProviderCall.started_at >= start, AIProviderCall.started_at < start + timedelta(days=1))
    totals = usage_totals(query)
    usage = db.query(AIUsageDaily).filter_by(owner_id=user.id, usage_date=day).first()
    used = usage.call_count if usage is not None else 0
    totals["token_usage_complete"] = totals["token_usage_complete"] and used <= totals["generation_attempts"]
    features = [value for (value,) in query.with_entities(AIProviderCall.feature).distinct().all()]
    return {
        "usage_date": day, "daily_generation_limit": DAILY_AI_CALL_BUDGET, "generation_budget_used": used,
        "generation_budget_exempt": AbuseControlService(db)._budget_exempt(user.id),
        "unitemized_generation_reservations": max(0, used - totals["generation_attempts"]),
        "totals": totals,
        "features": {feature: usage_totals(query.filter(AIProviderCall.feature == feature)) for feature in features},
        "recent_calls": query.order_by(AIProviderCall.started_at.desc(), AIProviderCall.id.desc()).limit(limit).all(),
    }
