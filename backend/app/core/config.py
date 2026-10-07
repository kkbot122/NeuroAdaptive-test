from pydantic_settings import BaseSettings
from pydantic import ConfigDict, Field, field_validator, model_validator

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
    # Model id is a setting, not a literal at the call site. Providers retire
    # models without notice — llama-3.3-70b-versatile was removed and every
    # chat request began returning 404 — and a retirement should be a config
    # change, not a code edit in two places.
    GROQ_MODEL: str = "openai/gpt-oss-120b"

    # Gemini — generation, multimodal and embeddings.
    # Model ids are explicit settings, not literals at the call site, so a
    # model change is configuration rather than a code edit.
    GEMINI_API_KEY: str = ""
    # Account/model eligibility must be verified before live deployment.
    GEMINI_GENERATION_MODEL: str = "gemini-3.5-flash-lite"
    GEMINI_EMBEDDING_MODEL: str = "gemini-embedding-001"
    # Bounded provider calls keep a stalled upstream request from holding a
    # durable processing stage indefinitely. This is an unvalidated V1
    # operational default and can be tuned through deployment configuration.
    GEMINI_GENERATION_TIMEOUT_SECONDS_V1: int = 45
    # One corrected re-prompt is allowed for malformed structured extraction
    # output; a second malformed result abstains rather than inventing data.
    CONCEPT_EXTRACTION_MAX_GENERATION_ATTEMPTS_V1: int = 2

    # Worker / queue. Redis coordinates tasks; PostgreSQL owns job state.
    CELERY_BROKER_URL: str = "redis://localhost:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/1"
    JOB_HEARTBEAT_SECONDS_V1: int = Field(default=15, gt=0)
    JOB_LEASE_SECONDS_V1: int = Field(default=120, gt=0)
    WORKER_TASK_SOFT_TIME_LIMIT_SECONDS_V1: int = Field(default=1200, gt=0)
    WORKER_TASK_TIME_LIMIT_SECONDS_V1: int = Field(default=1500, gt=0)
    EVALUATOR_EMAILS: str = ""  # comma-separated allowlist; closed by default

    # Indexed in bounded batches. These versioned values are unvalidated
    # defaults until the benchmark suite records representative measurements.
    INDEXING_BATCH_SIZE_V1: int = 10
    INDEXING_WORKER_CONCURRENCY_V1: int = 2

    # P2 preparation defaults. These are versioned operational/product
    # defaults, not calibrated values; changing them requires a new policy
    # version and does not change mastery thresholds or recommendation scores.
    P2_ASSESSMENT_DEFAULT_QUESTION_COUNT_V1: int = Field(default=5, ge=1, le=8)
    P2_ASSESSMENT_MAX_QUESTION_COUNT_V1: int = Field(default=8, ge=1, le=8)
    P2_MCQ_DEFAULT_DIFFICULTY_V1: float = Field(default=0.5, ge=0.0, le=1.0)
    # P4 fresh assessment sizes are approved defaults, not calibrated values.
    P4_REMEDIATION_QUESTION_COUNT_V1: int = Field(default=5, ge=1, le=8)
    P4_TARGETED_PRACTICE_QUESTION_COUNT_V1: int = Field(default=5, ge=1, le=8)
    P4_CHALLENGE_QUESTION_COUNT_V1: int = Field(default=5, ge=2, le=8)
    # P5 prepared sets retain their current size and replace one MCQ by one
    # grounded short answer by default. These are named, versioned, unvalidated
    # product/operational defaults, not calibrated learning rules.
    P5_SHORT_ANSWER_COUNT_V1: int = Field(default=1, ge=0, le=3)
    P5_RUBRIC_CRITERIA_COUNT_V1: int = Field(default=3, ge=1, le=5)
    P5_RUBRIC_PASSING_CRITERIA_V1: int = Field(default=2, ge=1, le=5)
    P5_GRADING_MAX_PROVIDER_CALLS_V1: int = Field(default=3, ge=1, le=5)
    P5_GRADING_DISPATCH_COOLDOWN_SECONDS_V1: int = Field(default=30, ge=5, le=300)
    P5_SHORT_ANSWER_MAX_CHARS_V1: int = Field(default=3000, ge=200, le=10000)
    P5_REPORT_MAX_CHARS_V1: int = Field(default=2000, ge=100, le=10000)
    P5_REVIEW_PAGE_SIZE_V1: int = Field(default=50, ge=1, le=100)
    # Separate from EVALUATOR_EMAILS by design. Empty means review is closed.
    P5_GRADING_REVIEWER_EMAILS: str = ""
    P2_PREPARATION_MAX_CANDIDATES_V1: int = Field(default=3, ge=1, le=5)
    P2_PREPARATION_MAX_LOOKAHEAD_V1: int = Field(default=1, ge=0, le=1)
    P2_PREPARATION_MAX_SOURCE_CHUNKS_V1: int = Field(default=12, ge=1, le=24)

    # Private S3-compatible storage (Supabase Storage production endpoint).
    STORAGE_BUCKET: str = "neurolearn-sources"
    STORAGE_S3_ENDPOINT: str = ""
    STORAGE_S3_REGION: str = "us-east-1"
    STORAGE_S3_ACCESS_KEY: str = ""
    STORAGE_S3_SECRET_KEY: str = ""
    STORAGE_SIGNED_URL_TTL_SECONDS_V1: int = 900

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

    @model_validator(mode="after")
    def validate_worker_lease(self):
        if self.JOB_LEASE_SECONDS_V1 <= self.JOB_HEARTBEAT_SECONDS_V1:
            raise ValueError("JOB_LEASE_SECONDS_V1 must exceed JOB_HEARTBEAT_SECONDS_V1")
        if self.WORKER_TASK_TIME_LIMIT_SECONDS_V1 <= self.WORKER_TASK_SOFT_TIME_LIMIT_SECONDS_V1:
            raise ValueError("WORKER_TASK_TIME_LIMIT_SECONDS_V1 must exceed the soft time limit")
        if self.P2_ASSESSMENT_DEFAULT_QUESTION_COUNT_V1 > self.P2_ASSESSMENT_MAX_QUESTION_COUNT_V1:
            raise ValueError("P2 assessment default question count must not exceed its maximum")
        if max(
            self.P4_REMEDIATION_QUESTION_COUNT_V1,
            self.P4_TARGETED_PRACTICE_QUESTION_COUNT_V1,
            self.P4_CHALLENGE_QUESTION_COUNT_V1,
        ) > self.P2_ASSESSMENT_MAX_QUESTION_COUNT_V1:
            raise ValueError("P4 activity question counts must not exceed the P2 assessment maximum")
        if self.P5_SHORT_ANSWER_COUNT_V1 >= self.P2_ASSESSMENT_MAX_QUESTION_COUNT_V1:
            raise ValueError("P5 short-answer count must leave at least one MCQ in the assessment set")
        if self.P5_RUBRIC_PASSING_CRITERIA_V1 > self.P5_RUBRIC_CRITERIA_COUNT_V1:
            raise ValueError("P5 rubric passing threshold must not exceed its criterion count")
        return self

    model_config = ConfigDict(
        case_sensitive=True,
        hide_input_in_errors=True,
        env_file=".env",
        extra="ignore",
    )

settings = Settings()
