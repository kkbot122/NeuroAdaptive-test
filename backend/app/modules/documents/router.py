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
from app.modules.documents.models import DocumentRole
from app.modules.documents.service import (
    DocumentNotFound,
    DocumentService,
    SourcesLocked,
    UploadRejected,
)

router = APIRouter()


def _service(db: Session = Depends(get_db)) -> DocumentService:
    return DocumentService(db)


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


@router.post("/courses/{course_id}/documents")
async def upload_document(
    course_id: UUID,
    file: UploadFile = File(...),
    role: str = Form(DocumentRole.STUDY.value),
    user: User = Depends(get_current_user),
    service: DocumentService = Depends(_service),
):
    content = await file.read()
    try:
        document, created = service.upload(
            course_id=course_id,
            owner_id=user.id,
            filename=file.filename or "",
            content=content,
            role=role,
            content_type=file.content_type,
        )
    except DocumentNotFound:
        raise HTTPException(status_code=404, detail="Course not found")
    except SourcesLocked as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except UploadRejected as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # 201 for a genuinely new document; 200 on a checksum dedup hit, since
    # nothing was created -- the existing document and its processed
    # artifacts (if any) are simply returned.
    return JSONResponse(status_code=201 if created else 200, content=jsonable_encoder(_out(document)))


@router.post("/courses/{course_id}/documents/paste")
def paste_text_document(
    course_id: UUID,
    body: PasteTextIn,
    user: User = Depends(get_current_user),
    service: DocumentService = Depends(_service),
):
    """
    Pasted text as a fourth ingestion path that skips upload entirely -- for
    a learner who wants to try the system on a paragraph or two rather than
    a whole file.
    """
    try:
        document, created = service.paste_text(
            course_id=course_id,
            owner_id=user.id,
            title=body.title or "",
            text=body.text,
            role=body.role,
        )
    except DocumentNotFound:
        raise HTTPException(status_code=404, detail="Course not found")
    except SourcesLocked as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except UploadRejected as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return JSONResponse(status_code=201 if created else 200, content=jsonable_encoder(_out(document)))


@router.get("/courses/{course_id}/documents")
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

    return Response(
        content=content,
        media_type=document.content_type or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{document.filename}"'},
    )
