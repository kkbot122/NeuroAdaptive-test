from app.modules.documents.storage import StorageUnavailable
from app.core.problem_details import ProblemDetailException
from app.modules.documents.schemas import DocumentMutationOut, DocumentOut, UploadIntentOut
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.session import get_db
from app.modules.auth.models import User
from app.modules.courses.service import CourseNotFound
from app.modules.documents.models import Document, DocumentRole
from app.modules.documents.service import (
    DocumentNotFound,
    DocumentService,
    SourcesLocked,
    UploadRejected,
    UploadIntentNotFound,
    UploadIntentConflict,
)
from app.modules.jobs.dispatch import CeleryJobDispatcher, JobDispatcher
from app.modules.jobs.service import JobAlreadyActive, JobService

router = APIRouter()


def _service(db: Session = Depends(get_db)) -> DocumentService:
    return DocumentService(db)


def _job_service(db: Session = Depends(get_db)) -> JobService:
    return JobService(db)


def _job_dispatcher() -> JobDispatcher:
    return CeleryJobDispatcher()


def _out(document) -> dict:
    return {
        "id": str(document.id),
        "course_id": str(document.course_id),
        "filename": document.filename,
        "role": document.role,
        "source_kind": document.source_kind,
        "status": document.status,
        "size_bytes": document.size_bytes,
        "page_count": document.page_count,
        "needs_input_reason": document.needs_input_reason,
        "created_at": document.created_at,
    }


class PasteTextIn(BaseModel):
    title: Optional[str] = Field(default=None, max_length=200)
    text: str = Field(min_length=1)
    role: str = DocumentRole.STUDY.value


class UploadIntentIn(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(gt=0, le=25 * 1024 * 1024)
    checksum_sha256: str = Field(min_length=64, max_length=64)
    role: str = DocumentRole.STUDY.value
    content_type: Optional[str] = Field(default=None, max_length=128)
    replaces_document_id: Optional[UUID] = None


def _queue_source_rebuild(course_id, owner_id, service, jobs, dispatcher):
    course = service.courses.get_owned(course_id, owner_id)
    if course.status != "PROCESSING":
        return None
    if not service.db.query(Document).filter(Document.course_id == course_id, Document.owner_id == owner_id).first():
        return None
    latest = jobs.get_latest_for_course(course_id, owner_id)
    if latest is not None and latest.source_revision == course.source_revision:
        return latest.id
    try:
        job = jobs.create_for_course(course_id, owner_id)
    except JobAlreadyActive:
        latest = jobs.get_latest_for_course(course_id, owner_id)
        if latest is None or latest.source_revision != course.source_revision:
            raise
        return latest.id
    try:
        dispatcher.enqueue(job.id, owner_id)
    except Exception:
        job = jobs.mark_dispatch_failed(job.id, owner_id)
    return job.id


def _mutation_out(document, *, source_changed=False, replaced_filename=None,
                  cleanup_pending=False, rebuild_job_id=None):
    return {
        **_out(document),
        "source_changed": source_changed,
        "replaced_filename": replaced_filename,
        "cleanup_pending": cleanup_pending,
        "rebuild_job_id": str(rebuild_job_id) if rebuild_job_id else None,
    }


@router.post("/courses/{course_id}/documents/upload-intents", status_code=201, response_model=UploadIntentOut)
def create_upload_intent(
    course_id: UUID, body: UploadIntentIn, user: User = Depends(get_current_user),
    service: DocumentService = Depends(_service),
):
    try:
        intent, upload = service.create_upload_intent(
            course_id, user.id, body.filename, body.size_bytes,
            body.checksum_sha256, body.role, body.content_type, body.replaces_document_id,
        )
    except (DocumentNotFound, CourseNotFound):
        raise HTTPException(status_code=404, detail="Course not found")
    except StorageUnavailable as exc:
        code = "storage-unavailable" if exc.configured else "storage-not-configured"
        raise ProblemDetailException(status_code=503, type_=f"https://neurolearn.internal/problems/{code}",
            title="Private Storage Unavailable", detail=str(exc))
    except SourcesLocked as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except UploadRejected as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"intent_id": str(intent.id), "upload_url": upload.upload_url,
            "required_headers": upload.required_headers, "expires_at": intent.expires_at}


@router.post("/courses/{course_id}/documents/finalize/{intent_id}", status_code=201, response_model=DocumentMutationOut)
def finalize_upload(
    course_id: UUID, intent_id: UUID, user: User = Depends(get_current_user),
    service: DocumentService = Depends(_service),
    jobs: JobService = Depends(_job_service),
    dispatcher: JobDispatcher = Depends(_job_dispatcher),
):
    try:
        result = service.finalize_upload_result(course_id, intent_id, user.id)
        rebuild_job_id = _queue_source_rebuild(course_id, user.id, service, jobs, dispatcher) if result.source_changed else None
        return _mutation_out(
            result.document,
            source_changed=result.source_changed,
            replaced_filename=result.replaced_filename,
            cleanup_pending=result.cleanup_pending,
            rebuild_job_id=rebuild_job_id,
        )
    except (CourseNotFound, UploadIntentNotFound):
        raise HTTPException(status_code=404, detail="Upload intent not found")
    except UploadIntentConflict:
        raise HTTPException(status_code=409, detail="This source changed before the replacement finished. Refresh the source list and try again.")
    except StorageUnavailable as exc:
        code = "storage-unavailable" if exc.configured else "storage-not-configured"
        raise ProblemDetailException(status_code=503, type_=f"https://neurolearn.internal/problems/{code}",
            title="Private Storage Unavailable", detail=str(exc))
    except SourcesLocked as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except UploadRejected as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/courses/{course_id}/documents", response_model=DocumentMutationOut, responses={201: {"model": DocumentMutationOut}})
async def upload_document(
    course_id: UUID,
    file: UploadFile = File(...),
    role: str = Form(DocumentRole.STUDY.value),
    replaces_document_id: Optional[UUID] = Form(None),
    user: User = Depends(get_current_user),
    service: DocumentService = Depends(_service),
    jobs: JobService = Depends(_job_service),
    dispatcher: JobDispatcher = Depends(_job_dispatcher),
):
    content = await file.read()
    try:
        course = service.courses.get_owned(course_id, user.id)
        revision_before = course.source_revision
        prior = next((doc for doc in service.list_for_course(course_id, user.id)
                      if doc.id == replaces_document_id), None) if replaces_document_id else None
        document, created = service.upload(
            course_id=course_id,
            owner_id=user.id,
            filename=file.filename or "",
            content=content,
            role=role,
            content_type=file.content_type,
            replaces_document_id=replaces_document_id,
        )
    except (DocumentNotFound, CourseNotFound):
        raise HTTPException(status_code=404, detail="Course not found")
    except StorageUnavailable as exc:
        code = "storage-unavailable" if exc.configured else "storage-not-configured"
        raise ProblemDetailException(status_code=503, type_=f"https://neurolearn.internal/problems/{code}",
            title="Private Storage Unavailable", detail=str(exc))
    except SourcesLocked as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except UploadRejected as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # 201 for a genuinely new document; 200 on a checksum dedup hit, since
    # nothing was created -- the existing document and its processed
    # artifacts (if any) are simply returned.
    course = service.courses.get_owned(course_id, user.id)
    source_changed = course.source_revision > revision_before
    rebuild_job_id = _queue_source_rebuild(course_id, user.id, service, jobs, dispatcher) if source_changed else None
    payload = _mutation_out(
        document, source_changed=source_changed,
        replaced_filename=prior.filename if source_changed and prior is not None else None,
        cleanup_pending=source_changed and prior is not None,
        rebuild_job_id=rebuild_job_id,
    )
    return JSONResponse(status_code=201 if created else 200, content=jsonable_encoder(DocumentMutationOut.model_validate(payload)))


@router.post("/courses/{course_id}/documents/paste", response_model=DocumentMutationOut, responses={201: {"model": DocumentMutationOut}})
def paste_text_document(
    course_id: UUID,
    body: PasteTextIn,
    user: User = Depends(get_current_user),
    service: DocumentService = Depends(_service),
    jobs: JobService = Depends(_job_service),
    dispatcher: JobDispatcher = Depends(_job_dispatcher),
):
    """
    Pasted text as a fourth ingestion path that skips upload entirely -- for
    a learner who wants to try the system on a paragraph or two rather than
    a whole file.
    """
    try:
        revision_before = service.courses.get_owned(course_id, user.id).source_revision
        document, created = service.paste_text(
            course_id=course_id,
            owner_id=user.id,
            title=body.title or "",
            text=body.text,
            role=body.role,
        )
    except (DocumentNotFound, CourseNotFound):
        raise HTTPException(status_code=404, detail="Course not found")
    except StorageUnavailable as exc:
        code = "storage-unavailable" if exc.configured else "storage-not-configured"
        raise ProblemDetailException(status_code=503, type_=f"https://neurolearn.internal/problems/{code}",
            title="Private Storage Unavailable", detail=str(exc))
    except SourcesLocked as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except UploadRejected as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    course = service.courses.get_owned(course_id, user.id)
    source_changed = course.source_revision > revision_before
    rebuild_job_id = _queue_source_rebuild(course_id, user.id, service, jobs, dispatcher) if source_changed else None
    payload = _mutation_out(document, source_changed=source_changed, rebuild_job_id=rebuild_job_id)
    return JSONResponse(status_code=201 if created else 200, content=jsonable_encoder(DocumentMutationOut.model_validate(payload)))


@router.delete("/courses/{course_id}/documents/{document_id}", response_model=DocumentMutationOut)
def remove_document(
    course_id: UUID, document_id: UUID, user: User = Depends(get_current_user),
    service: DocumentService = Depends(_service), jobs: JobService = Depends(_job_service),
    dispatcher: JobDispatcher = Depends(_job_dispatcher),
):
    try:
        revision_before = service.courses.get_owned(course_id, user.id).source_revision
        retired = service.get_owned(document_id, user.id)
        filename, _remaining, cleanup_pending = service.remove_source(course_id, document_id, user.id)
        course = service.courses.get_owned(course_id, user.id)
        source_changed = course.source_revision > revision_before
        rebuild_job_id = _queue_source_rebuild(course_id, user.id, service, jobs, dispatcher) if source_changed else None
        return {
            "id": str(document_id), "course_id": str(course_id), "filename": filename,
            "role": retired.role, "source_kind": retired.source_kind, "status": "RETIRED",
            "size_bytes": retired.size_bytes, "page_count": retired.page_count, "needs_input_reason": None,
            "created_at": retired.created_at, "source_changed": source_changed,
            "replaced_filename": filename, "cleanup_pending": cleanup_pending,
            "rebuild_job_id": str(rebuild_job_id) if rebuild_job_id else None,
        }
    except (DocumentNotFound, CourseNotFound):
        raise HTTPException(status_code=404, detail="Document not found")
    except SourcesLocked as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except UploadRejected as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/courses/{course_id}/documents", response_model=list[DocumentOut])
def list_documents(
    course_id: UUID,
    user: User = Depends(get_current_user),
    service: DocumentService = Depends(_service),
):
    from app.modules.courses.service import CourseNotFound

    try:
        return [_out(d) for d in service.list_for_course(course_id, user.id)]
    except CourseNotFound:
        raise HTTPException(status_code=404, detail="Course not found")


@router.get("/documents/{document_id}/content")
def download_document(
    document_id: UUID,
    user: User = Depends(get_current_user),
    service: DocumentService = Depends(_service),
):
    """
    The only read path for an uploaded original. Never served statically:
    ownership is checked on every request.
    """
    try:
        document = service.get_owned(document_id, user.id)
        content = service.read_bytes(document)
    except DocumentNotFound:
        raise HTTPException(status_code=404, detail="Document not found")
    except StorageUnavailable:
        raise HTTPException(status_code=503, detail="Private storage is unavailable")

    return Response(
        content=content,
        media_type=document.content_type or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{document.filename}"'},
    )
