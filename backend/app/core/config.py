from pydantic_settings import BaseSettings
from pydantic import ConfigDict, field_validator

# Values that shipped as defaults in earlier revisions of this file. They are
# public knowledge (they are in the git history), so a deployment that sets one
# of them is no better off than a deployment that sets nothing.
_KNOWN_INSECURE = {
    "dev_secret_key_123",
    "CHANGE_ME_TO_A_RANDOM_SECRET_KEY",
    "changeme",
    "secret",
}


class Settings(BaseSettings):
    PROJECT_NAME: str = "Backend Service"
    API_V1_STR: str = "/api/v1"

    # Database
    DATABASE_URL: str = "postgresql://postgres:password@localhost:5432/neuro_db"

    # INTERNAL AUTH — shared secret proving a request came from the Next.js
    # server rather than the browser. Required: no default, because a default
    # here fails open.
    INTERNAL_API_KEY: str

    # JWT
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24 hours

    # Google OAuth2
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""

    # Frontend URL (for CORS)
    FRONTEND_URL: str = "http://localhost:3000"

    # Groq (legacy chat path)
    GROQ_API_KEY: str = ""

    # Gemini — generation, multimodal and embeddings.
    # Model ids are explicit settings, not literals at the call site, so a
    # model change is configuration rather than a code edit.
    GEMINI_API_KEY: str = ""
    GEMINI_GENERATION_MODEL: str = "gemini-2.5-flash-lite"
    GEMINI_EMBEDDING_MODEL: str = "gemini-embedding-001"

    @field_validator("INTERNAL_API_KEY", "SECRET_KEY")
    @classmethod
    def _reject_weak_secret(cls, v: str, info) -> str:
        """
        Fail at startup rather than serve requests with a guessable secret.

        A short or publicly-known value is worse than a missing one, because a
        missing one is obvious and a weak one silently looks like it works.
        """
        if v in _KNOWN_INSECURE:
            raise ValueError(
                f"{info.field_name} is set to a publicly-known placeholder. "
                "Generate one with: python -c \"import secrets; "
                "print(secrets.token_urlsafe(32))\""
            )
        if len(v) < 32:
            raise ValueError(
                f"{info.field_name} must be at least 32 characters "
                f"(got {len(v)})."
            )
        return v

    model_config = ConfigDict(
        case_sensitive=True,
        env_file=".env",
        extra="ignore",
    )

settings = Settings()