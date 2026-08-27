from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.session import get_db
from app.modules.auth.models import User
from app.modules.content.models import Article, ArticleReading
from app.modules.profiling.models import UserProfile
from app.services.adaptation import adaptation_service, archetype_to_scores

router = APIRouter()


def _resolve_raw_scores(profile: UserProfile | None) -> dict:
    """
    Produce the raw_scores dict the adaptation engine expects.

    Mirrors the precedence used by the chat router: an explicitly calibrated
    raw_scores wins, then the legacy archetype mapped through the back-compat
    table, then the neutral default. adapt_content takes a dict, never a label.
    """
    if profile is None:
        return archetype_to_scores("THE_PIONEER")
    if profile.raw_scores:
        return dict(profile.raw_scores)
    return archetype_to_scores(profile.primary_archetype or "THE_PIONEER")


@router.get("/articles/{article_id}")
async def get_article(
    article_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # 1. Fetch the Article
    article = db.query(Article).filter(Article.id == article_id).first()
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")

    # 2. Log the Reading Session against the authenticated caller
    reading = ArticleReading(user_id=user.id, article_id=article.id)
    db.add(reading)
    db.commit()

    # 3. Resolve the caller's adaptation profile
    profile = (
        db.query(UserProfile).filter(UserProfile.user_id == user.id).first()
    )
    raw_scores = _resolve_raw_scores(profile)

    # 4. Construct Dual-Content Response
    # We map the database objects to a dictionary so we can include 'adapted_text'
    # without modifying the actual database records.
    adapted_paragraphs = []
    for p in article.paragraphs:
        adapted_text = await adaptation_service.adapt_content(
            p.original_text,
            raw_scores,
        )  # The AI-transformed version

        adapted_paragraphs.append({
            "id": p.id,
            "order_index": p.order_index,
            "original_text": p.original_text,  # The raw text from DB
            "adapted_text": adapted_text,
        })

    return {
        "id": article.id,
        "title": article.title,
        "topic": article.topic,
        "paragraphs": sorted(adapted_paragraphs, key=lambda x: x["order_index"]),
    }
