from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.problem_details import ProblemDetailException
from app.core.security import get_current_user
from app.db.session import get_db
from app.modules.abuse.service import AbuseControlService
from app.modules.auth.models import User
from app.modules.courses.service import CourseNotFound, CourseService
from app.modules.jobs.service import JobNotFound, JobService

router = APIRouter()


def _service(db: Session = Depends(get_db)) -> JobService:
    return JobService(db)


def _out(job) -> dict:
    return {
        "id": str(job.id),
        "course_id": str(job.course_id),
        "status": job.status,
        "current_stage": job.current_stage,
        "retry_count": job.retry_count,
        "error_category": job.error_category,
        "error_detail": job.error_detail,
        "stages": [
            {
                "name": s.name,
                "position": s.position,
                "status": s.status,
                "attempts": s.attempts,
                "error_category": s.error_category,
            }
            for s in job.stages
        ],
    }


@router.post("/courses/{course_id}/process", status_code=202)
def start_processing(
    course_id: UUID,
    user: User = Depends(get_current_user),
    service: JobService = Depends(_service),
    db: Session = Depends(get_db),
):
    """Create and run a processing job for a course the caller owns."""
    try:
        CourseService(db).get_owned(course_id, user.id)
    except CourseNotFound:
        raise HTTPException(status_code=404, detail="Course not found")

    # T5: checked here, before the job (and the synchronous pipeline run
    # behind it -- service.run() below executes in-process, not on a queue)
    # starts, so an over-cap request never gets billed for generation work
    # only to fail partway through.
    AbuseControlService(db).enforce_course_regeneration_cap(course_id, user.id)

    # Concurrency guard: a UI double/triple-click (no loading feedback on
    # the button) sent two overlapping requests for the same course, which
    # raced on inserting the same deterministic chunk id and crashed with a
    # real IntegrityError -- reproduced live. Reject the second call
    # outright rather than letting two pipeline runs collide.
    if service.has_active_job_for_course(course_id):
        raise ProblemDetailException(
            status_code=409,
            type_="https://neurolearn.internal/problems/processing-already-running",
            title="Processing Already In Progress",
            detail="This course is already being processed. Wait for it to finish before starting another run.",
        )

    job = service.create_for_course(course_id, user.id)
    service.run(job.id, user.id)
    return _out(service.get_owned(job.id, user.id))


@router.get("/jobs/{job_id}")
def get_job(
    job_id: UUID,
    user: User = Depends(get_current_user),
    service: JobService = Depends(_service),
):
    try:
        return _out(service.get_owned(job_id, user.id))
    except JobNotFound:
        raise HTTPException(status_code=404, detail="Job not found")


@router.post("/jobs/{job_id}/retry")
def retry_job(
    job_id: UUID,
    user: User = Depends(get_current_user),
    service: JobService = Depends(_service),
    db: Session = Depends(get_db),
):
    """
    Resume a paused or failed job. Stages that already succeeded are not
    re-run, so a retry never duplicates completed work.
    """
    try:
        job = service.get_owned(job_id, user.id)
    except JobNotFound:
        raise HTTPException(status_code=404, detail="Job not found")

    job.retry_count += 1
    db.commit()
    service.run(job.id, user.id)
    return _out(service.get_owned(job.id, user.id))
