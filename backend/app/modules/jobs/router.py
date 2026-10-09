from app.modules.jobs.schemas import JobOut
from uuid import UUID
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.problem_details import ProblemDetailException
from app.core.security import get_current_user
from app.db.session import get_db
from app.modules.abuse.service import AbuseControlService
from app.modules.auth.models import User
from app.modules.courses.service import CourseNotFound, CourseService
from app.modules.jobs.service import JobAlreadyActive, JobNotRetryable, JobNotFound, JobService
from app.modules.jobs.dispatch import CeleryJobDispatcher, JobDispatcher

router = APIRouter()


def _service(db: Session = Depends(get_db)) -> JobService:
    return JobService(db)


def _dispatcher() -> JobDispatcher:
    return CeleryJobDispatcher()


def _out(job) -> dict:
    expiry = job.lease_expires_at
    if expiry is not None and expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    interrupted = job.status == "RUNNING" and (expiry is None or expiry <= datetime.now(timezone.utc))
    return {
        "id": str(job.id),
        "course_id": str(job.course_id),
        "source_revision": job.source_revision,
        "status": job.status,
        "current_stage": job.current_stage,
        "retry_available": interrupted or job.status in ("PAUSED", "FAILED", "NEEDS_INPUT"),
        "retry_count": job.retry_count,
        "error_category": "INTERRUPTED" if interrupted else job.error_category,
        "error_detail": "Worker heartbeat stopped. Retry processing to resume." if interrupted else job.error_detail,
        "stages": [
            {
                "name": s.name,
                "position": s.position,
                "status": s.status,
                "attempts": s.attempts,
                "error_category": s.error_category,
                "input_count": s.input_count,
                "output_count": s.output_count,
                "provider_call_count": s.provider_call_count,
                "started_at": s.started_at,
                "finished_at": s.finished_at,
            }
            for s in job.stages
        ],
    }


@router.post("/courses/{course_id}/process", status_code=202, response_model=JobOut)
def start_processing(
    course_id: UUID,
    user: User = Depends(get_current_user),
    service: JobService = Depends(_service),
    db: Session = Depends(get_db),
    dispatcher: JobDispatcher = Depends(_dispatcher),
):
    """Create and run a processing job for a course the caller owns."""
    courses = CourseService(db)
    try:
        course = courses.get_owned(course_id, user.id)
    except CourseNotFound:
        raise HTTPException(status_code=404, detail="Course not found")

    # Concurrency guard: a UI double/triple-click (no loading feedback on
    # the button) sent two overlapping requests for the same course, which
    # raced on inserting the same deterministic chunk id and crashed with a
    # real IntegrityError -- reproduced live. Reuse the current-revision
    # active job so a duplicate request cannot start a second pipeline.
    active = service.get_latest_for_course(course_id, user.id)
    if active is not None and active.source_revision == course.source_revision and active.status in ("PENDING", "RUNNING"):
        return _out(active)

    # Enforce quotas before committing a new job. Duplicate requests above
    # reuse the same durable job and do not spend another regeneration slot.
    AbuseControlService(db).enforce_course_regeneration_cap(course_id, user.id)

    # Processing is the immutable-source boundary. This is intentionally in
    # the committed finalization boundary before dispatch: a worker can never observe a
    # mutable source set.
    if not course.sources_are_finalized:
        courses.finalize_sources(course_id, user.id)
    try:
        job = service.create_for_course(course_id, user.id)
    except JobAlreadyActive:
        raise HTTPException(status_code=409, detail="This course already has an active processing job")
    try:
        dispatcher.enqueue(job.id, user.id)
    except Exception:
        job = service.mark_dispatch_failed(job.id, user.id)
    return _out(job)


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(
    job_id: UUID,
    user: User = Depends(get_current_user),
    service: JobService = Depends(_service),
):
    try:
        return _out(service.get_owned(job_id, user.id))
    except JobNotFound:
        raise HTTPException(status_code=404, detail="Job not found")


@router.get("/courses/{course_id}/jobs/latest", response_model=JobOut | None)
def get_latest_job(
    course_id: UUID,
    user: User = Depends(get_current_user),
    service: JobService = Depends(_service),
    db: Session = Depends(get_db),
):
    """
    So a client can recover "is this course mid-processing, paused, or
    never started" without already holding a job id in memory -- the only
    other way to learn one is the response of the call that created it,
    which a page reload or navigating away loses. Returns null (200), not
    404, when processing has never been started for this course: that is
    an ordinary state, not an error.
    """
    try:
        CourseService(db).get_owned(course_id, user.id)
    except CourseNotFound:
        raise HTTPException(status_code=404, detail="Course not found")

    job = service.get_latest_for_course(course_id, user.id)
    return _out(job) if job else None


@router.post("/jobs/{job_id}/retry", status_code=202, response_model=JobOut)
def retry_job(
    job_id: UUID,
    user: User = Depends(get_current_user),
    service: JobService = Depends(_service),
    db: Session = Depends(get_db),
    dispatcher: JobDispatcher = Depends(_dispatcher),
):
    """
    Resume a paused or failed job. Stages that already succeeded are not
    re-run, so a retry never duplicates completed work.

    Runs in the background like start_processing (9bc8cfb) -- this used to
    call service.run() inline and block the request for the full remaining
    pipeline duration, the same false-502-under-the-proxy's-timeout failure
    mode start_processing had before that fix, just never caught here.
    """
    try:
        job = service.get_owned(job_id, user.id)
    except JobNotFound:
        raise HTTPException(status_code=404, detail="Job not found")

    try:
        job = service.prepare_retry(job_id, user.id)
    except (JobNotRetryable, JobAlreadyActive):
        raise HTTPException(status_code=409, detail="The job is not retryable or another job is active")
    try:
        dispatcher.enqueue(job.id, user.id)
    except Exception:
        job = service.mark_dispatch_failed(job.id, user.id)
    return _out(job)
