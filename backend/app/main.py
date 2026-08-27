from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings

# --- Import Models so SQLAlchemy registers them ---
# Still required without create_all: the string-based relationship() targets
# below are resolved against whatever is registered on Base.metadata.
from app.modules.auth import models as auth_models
from app.modules.content import models as content_models
from app.modules.profiling import models as profiling_models
from app.modules.chat import models as chat_models

# --- Import Routers ---
from app.modules.auth.router import router as auth_router
from app.modules.content.router import router as content_router
from app.modules.profile.router import router as profile_router
from app.modules.chat.router import router as chat_router

app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
)

# Schema is owned by Alembic. `Base.metadata.create_all()` used to run here,
# which meant two mechanisms could shape the database and silently diverge:
# create_all never alters an existing table, so any migration that changed a
# column applied only where the table did not already exist.
#
#     alembic upgrade head
#
# --- CORS configuration ---
origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    str(settings.FRONTEND_URL),
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# --- Register API Routers ---
app.include_router(auth_router, prefix=f"{settings.API_V1_STR}/auth", tags=["auth"])
app.include_router(content_router, prefix=f"{settings.API_V1_STR}/content", tags=["content"])
app.include_router(profile_router, prefix=f"{settings.API_V1_STR}/profile", tags=["profile"])
app.include_router(chat_router, prefix=f"{settings.API_V1_STR}/chat", tags=["chat"])

# --- Health check ---
@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "service": settings.PROJECT_NAME,
    }