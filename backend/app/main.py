from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.problem_details import ProblemDetailException, problem_detail_exception_handler
from app.db.session import get_db

# --- Import Models so SQLAlchemy registers them ---
# Still required without create_all: the string-based relationship() targets
# below are resolved against whatever is registered on Base.metadata.
from app.modules.auth import models as auth_models
from app.modules.content import models as content_models
from app.modules.profiling import models as profiling_models
from app.modules.chat import models as chat_models
from app.modules.events import models as events_models
from app.modules.assessment import models as assessment_models
from app.modules.courses import models as courses_models
from app.modules.documents import models as documents_models
from app.modules.jobs import models as jobs_models
from app.modules.documents import chunk_models as chunk_models
from app.modules.curriculum import models as curriculum_models
from app.modules.mastery import models as mastery_models
from app.modules.adaptation import models as adaptation_models
from app.modules.tutor import models as tutor_models
from app.modules.abuse import models as abuse_models
from app.modules.audit import models as audit_models
from app.modules.evaluation import models as evaluation_models

# --- Import Routers ---
from app.modules.auth.router import router as auth_router
from app.modules.content.router import router as content_router
from app.modules.profile.router import router as profile_router
from app.modules.chat.router import router as chat_router
from app.modules.events.router import router as events_router
from app.modules.assessment.router import router as assessment_router
from app.modules.retrieval.router import router as retrieval_router
from app.modules.curriculum.router import router as curriculum_router
from app.modules.courses.router import router as courses_router
from app.modules.identity.router import router as identity_router
from app.modules.identity.health import router as health_router
from app.modules.documents.router import router as documents_router
from app.modules.jobs.router import router as jobs_router
from app.modules.mastery.router import router as mastery_router
from app.modules.adaptation.router import router as adaptation_router
from app.modules.tutor.router import router as tutor_router
from app.modules.evaluation.router import router as evaluation_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    # jobs/service.py runs the pipeline in-process, not on a queue -- a job
    # stuck PENDING or RUNNING can therefore only mean the process that owned
    # it died mid-run (crash, OOM, container restart) before it could mark
    # the job FAILED. Left alone, has_active_job_for_course() (jobs/service.py)
    # would reject every future /process call for that course with a 409
    # forever, since no other process is ever coming back to finish it.
    # Reclaim any such job as FAILED/INTERRUPTED on startup so a fresh
    # attempt is unblocked.
    from app.modules.courses.models import Course, CourseStatus
    from app.modules.jobs.models import JobStatus, ProcessingJob, StageStatus

    # Route through whatever get_db is overridden to (tests point this at an
    # isolated in-memory session via app.dependency_overrides -- see
    # conftest.py's `client` fixture). TestClient runs this lifespan on every
    # `with TestClient(app) as c:` entry, so hitting the real, schema-less
    # settings.DATABASE_URL engine directly broke every single test with
    # "no such table: processing_jobs" the first time this was tried.
    db_dependency = app.dependency_overrides.get(get_db, get_db)
    db_gen = db_dependency()
    db = next(db_gen)
    try:
        orphaned = (
            db.query(ProcessingJob)
            .filter(ProcessingJob.status.in_([JobStatus.PENDING.value, JobStatus.RUNNING.value]))
            .all()
        )
        for job in orphaned:
            job.status = JobStatus.FAILED.value
            job.error_category = "INTERRUPTED"
            job.finished_at = datetime.now(timezone.utc)
            for stage in job.stages:
                if stage.status == StageStatus.RUNNING.value:
                    stage.status = StageStatus.FAILED.value
                    stage.finished_at = datetime.now(timezone.utc)
            course = db.query(Course).filter(Course.id == job.course_id).first()
            if course is not None:
                course.status = CourseStatus.FAILED.value
        if orphaned:
            db.commit()
    finally:
        try:
            next(db_gen)
        except StopIteration:
            pass
    yield


app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    lifespan=lifespan,
)
app.add_exception_handler(ProblemDetailException, problem_detail_exception_handler)

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
app.include_router(events_router, prefix=f"{settings.API_V1_STR}/events", tags=["events"])
app.include_router(assessment_router, prefix=f"{settings.API_V1_STR}/assessment", tags=["assessment"])
app.include_router(retrieval_router, prefix=settings.API_V1_STR, tags=["retrieval"])
app.include_router(curriculum_router, prefix=settings.API_V1_STR, tags=["curriculum"])
app.include_router(courses_router, prefix=f"{settings.API_V1_STR}/courses", tags=["courses"])
app.include_router(identity_router, prefix=settings.API_V1_STR, tags=["identity"])
app.include_router(documents_router, prefix=settings.API_V1_STR, tags=["documents"])
app.include_router(jobs_router, prefix=settings.API_V1_STR, tags=["jobs"])
app.include_router(mastery_router, prefix=settings.API_V1_STR, tags=["mastery"])
app.include_router(adaptation_router, prefix=settings.API_V1_STR, tags=["adaptation"])
app.include_router(tutor_router, prefix=settings.API_V1_STR, tags=["tutor"])
app.include_router(evaluation_router, prefix=settings.API_V1_STR, tags=["evaluation"])

# --- Health checks (liveness + database readiness) ---
app.include_router(health_router, tags=["health"])