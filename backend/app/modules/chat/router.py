import logging

from fastapi import APIRouter, Depends, HTTPException, Header, UploadFile, File, Form
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.modules.auth.models import User
from app.core.config import settings
from typing import Optional

from app.services.adaptation import (
    get_client,
    build_adaptive_system_prompt,
    build_fslsm_system_prompt,
    archetype_to_scores,
    QUIZ_INSTRUCTIONS,
    infer_signals_from_prompt,
    apply_signals_to_scores,
)
from app.modules.profiling.models import UserProfile
from app.modules.chat.models import ChatSession, ChatMessage

logger = logging.getLogger(__name__)

router = APIRouter()


def _sanitize(text: str) -> str:
    """
    Strip lone Unicode surrogates (U+D800–U+DFFF) that break UTF-8 encoding.
    These appear when Groq returns mathematical bold/italic Unicode
    (e.g. U+1D4N series) and the JSON parser creates an unpaired surrogate.
    encode('utf-8', errors='replace') replaces each lone surrogate with U+FFFD.
    """
    if not isinstance(text, str):
        return ""
    return text.encode("utf-8", errors="replace").decode("utf-8")


def _db_role_to_api_role(role: str) -> str:
    """Map internal DB role ('bot') to OpenAI-compatible API role ('assistant')."""
    return "assistant" if role == "bot" else role


async def get_current_user_chat(
    x_user_email: str = Header(...),
    x_internal_token: str = Header(...),
    db: Session = Depends(get_db),
) -> User:
    if x_internal_token != settings.INTERNAL_API_KEY:
        raise HTTPException(status_code=403, detail="Invalid Internal API Key")
    user = db.query(User).filter(User.email == x_user_email).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


# ─────────────────────────────────────────────────────────────────────────────
# Session management endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/sessions")
async def list_sessions(
    user: User = Depends(get_current_user_chat),
    db: Session = Depends(get_db),
):
    sessions = (
        db.query(ChatSession)
        .filter(ChatSession.user_id == user.id)
        .order_by(ChatSession.created_at.desc())
        .all()
    )
    return [
        {"id": s.id, "title": s.title or "New Session", "created_at": s.created_at}
        for s in sessions
    ]


@router.get("/sessions/{session_id}/messages")
async def get_session_messages(
    session_id: int,
    user: User = Depends(get_current_user_chat),
    db: Session = Depends(get_db),
):
    session = (
        db.query(ChatSession)
        .filter(ChatSession.id == session_id, ChatSession.user_id == user.id)
        .first()
    )
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    messages = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )
    return [
        {"role": m.role, "content": m.content, "created_at": m.created_at}
        for m in messages
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Main chat endpoint
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/message")
async def send_message(
    prompt: str = Form(...),
    session_id: Optional[int] = Form(None),
    file: UploadFile = File(None),
    user: User = Depends(get_current_user_chat),
    db: Session = Depends(get_db),
):
    if not getattr(settings, "GROQ_API_KEY", None) or "your_groq_api_key" in settings.GROQ_API_KEY:
        raise HTTPException(status_code=500, detail="Groq API Key is not configured.")

    # ── 1. Build personalized system prompt ──────────────────────────────────
    profile: Optional[UserProfile] = db.query(UserProfile).filter(
        UserProfile.user_id == user.id
    ).first()

    # Phase 3: prefer FSLSM vectors when available, fall back to archetype scores
    fslsm_vectors = None
    if profile:
        fslsm_vectors = getattr(profile, "fslsm_vectors", None)

    if fslsm_vectors and any(abs(v) > 0.01 for v in fslsm_vectors.values()):
        base_system_prompt = build_fslsm_system_prompt(fslsm_vectors)
    else:
        archetype = profile.primary_archetype if profile else "THE_PIONEER"
        scores = archetype_to_scores(archetype)
        base_system_prompt = build_adaptive_system_prompt(scores)

    system_prompt = base_system_prompt + QUIZ_INSTRUCTIONS

    # ── 2. Get or create chat session ────────────────────────────────────────
    if session_id:
        chat_session = (
            db.query(ChatSession)
            .filter(ChatSession.id == session_id, ChatSession.user_id == user.id)
            .first()
        )
        if not chat_session:
            raise HTTPException(status_code=404, detail="Session not found")
    else:
        title = (prompt[:47] + "…") if len(prompt) > 50 else prompt
        chat_session = ChatSession(user_id=user.id, title=title)
        db.add(chat_session)
        db.commit()
        db.refresh(chat_session)

    # ── 3. Fetch recent history (last 12 messages) ───────────────────────────
    history = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == chat_session.id)
        .order_by(ChatMessage.created_at.desc())
        .limit(12)
        .all()
    )
    history = sorted(history, key=lambda x: x.created_at)

    messages_for_llm: list[dict] = [{"role": "system", "content": system_prompt}]
    for h in history:
        # DB stores bot messages with role="bot"; Groq requires "assistant"
        messages_for_llm.append({
            "role": _db_role_to_api_role(h.role),
            "content": _sanitize(h.content),
        })

    # ── 4. Process file attachment if present ────────────────────────────────
    user_content = _sanitize(prompt)

    if file and file.filename:
        raw_bytes = await file.read()
        fname = (file.filename or "").lower()

        if fname.endswith(".pdf") or file.content_type == "application/pdf":
            import io
            try:
                from pypdf import PdfReader
                reader = PdfReader(io.BytesIO(raw_bytes))
                extracted = "\n".join(page.extract_text() or "" for page in reader.pages)
                user_content += f"\n\n[PDF: {file.filename}]\n{extracted}"
            except Exception as exc:
                raise HTTPException(
                    status_code=400, detail=f"Failed to parse PDF: {exc}"
                )
        else:
            try:
                text_content = raw_bytes.decode("utf-8")
                user_content += f"\n\n[File: {file.filename}]\n{text_content}"
            except UnicodeDecodeError:
                raise HTTPException(
                    status_code=400,
                    detail="Only text-based files (.txt, .md, .csv) and PDFs are supported.",
                )

    messages_for_llm.append({"role": "user", "content": user_content})

    # ── 5. Call Groq LLM ─────────────────────────────────────────────────────
    try:
        response = await get_client().chat.completions.create(
            model=settings.GROQ_MODEL,
            messages=messages_for_llm,
            temperature=0.7,
            max_tokens=2048,
        )
        bot_text: str = _sanitize(response.choices[0].message.content or "")
    except Exception as exc:
        # The provider's own error text was previously interpolated straight
        # into the response, so a learner saw raw JSON naming the model, the
        # provider error code and the request type. That leaks internals and
        # tells them nothing they can act on. Detail goes to the log; the
        # caller gets a category.
        status = getattr(exc, "status_code", None)
        logger.error(
            "Groq call failed: %s status=%s model=%s",
            type(exc).__name__,
            status,
            settings.GROQ_MODEL,
        )

        if status in (401, 403):
            detail = "The AI provider rejected our credentials. This is a server configuration problem, not something you did."
        elif status == 404:
            # Providers retire models without notice; this is the exact failure
            # that produced the 404 on llama-3.3-70b-versatile.
            detail = "The configured AI model is unavailable. This is a server configuration problem, not something you did."
        elif status == 429:
            detail = "The AI provider is rate limiting us. Wait a moment and try again."
        else:
            detail = "The AI service did not respond. Try again in a moment."

        raise HTTPException(status_code=502, detail=detail)

    # ── 6. Persist messages ──────────────────────────────────────────────────
    user_msg = ChatMessage(session_id=chat_session.id, role="user", content=_sanitize(prompt))
    bot_msg = ChatMessage(session_id=chat_session.id, role="bot", content=bot_text)
    db.add_all([user_msg, bot_msg])

    # ── 7. Behavioral signal inference → update raw_scores ───────────────────
    # Infer signals from the user's prompt and apply micro-deltas to their
    # cognitive profile so the adaptation engine evolves over time.
    if profile:
        signals = infer_signals_from_prompt(prompt)
        if signals:
            current_scores = dict(profile.raw_scores or {})
            updated_scores = apply_signals_to_scores(current_scores, signals)
            profile.raw_scores = updated_scores

    db.commit()

    return {"text": bot_text, "session_id": chat_session.id}
