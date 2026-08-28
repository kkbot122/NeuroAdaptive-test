"""
API tests for upload and the processing pipeline.

Covers the mandate's non-negotiables for this item: owner filters enforced
below the route, an uploaded file never served unauthenticated, and a document
with no extractable text landing on an explicit NEEDS_INPUT state with a
plain-language reason rather than silent empty output.
"""
import pytest

from app.modules.documents.chunk_models import Chunk
from app.modules.documents.models import Document
from tests.conftest import auth_headers

PROSE = ("Parsing is the process of analysing a string of symbols. " * 12).strip()


@pytest.fixture()
def course(client, owner):
    response = client.post(
        "/api/v1/courses", json={"title": "Compilers"}, headers=auth_headers(owner.email)
    )
    return response.json()


def upload(client, email, course_id, filename="notes.txt", content=None, role="STUDY"):
    return client.post(
        f"/api/v1/courses/{course_id}/documents",
        files={"file": (filename, content if content is not None else PROSE.encode(), "text/plain")},
        data={"role": role},
        headers=auth_headers(email),
    )


class TestUploadOwnership:
    def test_owner_can_upload(self, client, owner, course, db_session):
        response = upload(client, owner.email, course["id"])
        assert response.status_code == 201
        assert db_session.query(Document).one().owner_id == owner.id

    def test_other_user_cannot_upload_to_your_course(self, client, other_user, course, db_session):
        response = upload(client, other_user.email, course["id"])
        assert response.status_code == 404
        assert db_session.query(Document).count() == 0

    def test_other_user_cannot_list_your_documents(self, client, owner, other_user, course):
        upload(client, owner.email, course["id"])
        response = client.get(
            f"/api/v1/courses/{course['id']}/documents", headers=auth_headers(other_user.email)
        )
        assert response.status_code == 404

    def test_other_user_cannot_download_your_file(self, client, owner, other_user, course):
        doc_id = upload(client, owner.email, course["id"]).json()["id"]
        response = client.get(
            f"/api/v1/documents/{doc_id}/content", headers=auth_headers(other_user.email)
        )
        assert response.status_code == 404

    def test_download_requires_authentication(self, client, owner, course):
        """An uploaded original is never reachable unauthenticated."""
        doc_id = upload(client, owner.email, course["id"]).json()["id"]
        assert client.get(f"/api/v1/documents/{doc_id}/content").status_code == 422

    def test_owner_can_download_their_own(self, client, owner, course):
        doc_id = upload(client, owner.email, course["id"]).json()["id"]
        response = client.get(
            f"/api/v1/documents/{doc_id}/content", headers=auth_headers(owner.email)
        )
        assert response.status_code == 200
        assert b"Parsing" in response.content


class TestUploadValidation:
    def test_rejects_unsupported_extension(self, client, owner, course):
        response = upload(client, owner.email, course["id"], filename="deck.pptx")
        assert response.status_code == 400
        assert "Supported this release" in response.json()["detail"]

    def test_rejects_empty_file(self, client, owner, course):
        response = upload(client, owner.email, course["id"], content=b"")
        assert response.status_code == 400

    def test_enforces_the_study_file_cap(self, client, owner, course):
        for _ in range(2):
            assert upload(client, owner.email, course["id"]).status_code == 201
        third = upload(client, owner.email, course["id"])
        assert third.status_code == 400
        assert "maximum" in third.json()["detail"]

    def test_syllabus_has_its_own_cap(self, client, owner, course):
        assert upload(client, owner.email, course["id"], role="SYLLABUS").status_code == 201
        assert upload(client, owner.email, course["id"], role="SYLLABUS").status_code == 400

    def test_stored_filename_does_not_control_the_path(self, client, owner, course, db_session):
        """A learner-supplied name must never decide where bytes land."""
        upload(client, owner.email, course["id"], filename="../../escape.txt")
        document = db_session.query(Document).one()
        assert ".." not in document.storage_path
        assert document.filename == "escape.txt"

    def test_upload_refused_after_sources_finalized(self, client, owner, course):
        client.post(
            f"/api/v1/courses/{course['id']}/finalize-sources", headers=auth_headers(owner.email)
        )
        response = upload(client, owner.email, course["id"])
        assert response.status_code == 409


class TestPipeline:
    def test_runs_through_chunking_and_pauses(self, client, owner, course, db_session):
        """
        The implemented stages complete; the first unimplemented stage pauses
        the job rather than failing it, so no completed work is lost.
        """
        upload(client, owner.email, course["id"])
        response = client.post(
            f"/api/v1/courses/{course['id']}/process", headers=auth_headers(owner.email)
        )
        assert response.status_code == 202

        body = response.json()
        assert body["status"] == "PAUSED"
        assert body["error_category"] == "STAGE_NOT_IMPLEMENTED"

        done = {s["name"] for s in body["stages"] if s["status"] == "SUCCEEDED"}
        assert {"VALIDATING", "EXTRACTING", "CHUNKING"} <= done

    def test_produces_chunks_with_provenance(self, client, owner, course, db_session):
        upload(client, owner.email, course["id"])
        client.post(f"/api/v1/courses/{course['id']}/process", headers=auth_headers(owner.email))

        chunks = db_session.query(Chunk).all()
        assert chunks
        assert all(c.owner_id == owner.id for c in chunks)
        assert all(str(c.course_id) == course["id"] for c in chunks)
        assert all(c.page_start is not None for c in chunks)
        assert all(c.indexed_at is None for c in chunks)  # not yet embedded

    def test_scanned_pdf_lands_on_needs_input_with_a_reason(self, client, owner, course, db_session):
        """
        A *valid* PDF with no selectable text — the shape a scan takes — must
        say so in plain language, not produce nothing and not be reported as a
        corrupt file.
        """
        import io as _io

        from pypdf import PdfWriter

        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        buffer = _io.BytesIO()
        writer.write(buffer)
        blank_pdf = buffer.getvalue()

        upload(client, owner.email, course["id"], filename="scan.pdf", content=blank_pdf)
        response = client.post(
            f"/api/v1/courses/{course['id']}/process", headers=auth_headers(owner.email)
        )

        assert response.json()["status"] == "NEEDS_INPUT"
        document = db_session.query(Document).one()
        assert document.status == "NEEDS_INPUT"
        assert "scan" in (document.needs_input_reason or "").lower()

    def test_course_with_no_documents_fails_validation(self, client, owner, course):
        response = client.post(
            f"/api/v1/courses/{course['id']}/process", headers=auth_headers(owner.email)
        )
        assert response.json()["status"] == "FAILED"

    def test_rerunning_does_not_duplicate_chunks(self, client, owner, course, db_session):
        """Stage idempotency: retry must not append a second set of chunks."""
        upload(client, owner.email, course["id"])
        client.post(f"/api/v1/courses/{course['id']}/process", headers=auth_headers(owner.email))
        first = db_session.query(Chunk).count()

        client.post(f"/api/v1/courses/{course['id']}/process", headers=auth_headers(owner.email))
        assert db_session.query(Chunk).count() == first

    def test_rerunning_produces_the_same_chunk_ids(self, client, owner, course, db_session):
        """
        The actual point of deterministic ids: not just the same row count,
        but the identical id set, so a citation recorded elsewhere keeps
        pointing at the same chunk across a reprocess.
        """
        upload(client, owner.email, course["id"])
        client.post(f"/api/v1/courses/{course['id']}/process", headers=auth_headers(owner.email))
        first_ids = {c.id for c in db_session.query(Chunk).all()}

        client.post(f"/api/v1/courses/{course['id']}/process", headers=auth_headers(owner.email))
        second_ids = {c.id for c in db_session.query(Chunk).all()}

        assert first_ids == second_ids
        assert len(first_ids) > 0

    def test_chunks_carry_token_count_and_char_offsets(self, client, owner, course, db_session):
        upload(client, owner.email, course["id"])
        client.post(f"/api/v1/courses/{course['id']}/process", headers=auth_headers(owner.email))

        chunks = db_session.query(Chunk).all()
        assert chunks
        for c in chunks:
            assert c.token_count > 0
            assert c.char_start is not None and c.char_end is not None
            assert c.char_start < c.char_end
            assert c.extraction_version == 1


class TestJobOwnership:
    def test_other_user_cannot_read_your_job(self, client, owner, other_user, course):
        upload(client, owner.email, course["id"])
        job_id = client.post(
            f"/api/v1/courses/{course['id']}/process", headers=auth_headers(owner.email)
        ).json()["id"]

        response = client.get(f"/api/v1/jobs/{job_id}", headers=auth_headers(other_user.email))
        assert response.status_code == 404

    def test_other_user_cannot_retry_your_job(self, client, owner, other_user, course):
        upload(client, owner.email, course["id"])
        job_id = client.post(
            f"/api/v1/courses/{course['id']}/process", headers=auth_headers(owner.email)
        ).json()["id"]

        response = client.post(
            f"/api/v1/jobs/{job_id}/retry", headers=auth_headers(other_user.email)
        )
        assert response.status_code == 404

    def test_owner_can_poll_their_job(self, client, owner, course):
        upload(client, owner.email, course["id"])
        job_id = client.post(
            f"/api/v1/courses/{course['id']}/process", headers=auth_headers(owner.email)
        ).json()["id"]

        response = client.get(f"/api/v1/jobs/{job_id}", headers=auth_headers(owner.email))
        assert response.status_code == 200
        assert len(response.json()["stages"]) == 8
