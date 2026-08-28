"""
TutorService: retrieval -> bounded context -> source-only generation ->
two-tier citation validation -> strip/retry/abstain -> persist.

Every retrieval call goes through RetrievalService.search (Phase 1), which
already applies the owner_id/course_id filter INSIDE both the vector and
lexical queries -- this module adds no second retrieval path, so the
"ownership filter lives inside the query, never a post-filter" property
holds here for exactly the reason it holds in retrieval/service.py.
"""
from dataclasses import dataclass
from typing import List, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.modules.courses.service import CourseNotFound, CourseService
from app.modules.curriculum.models import Concept, CourseVersion, Lesson, Module
from app.modules.retrieval.service import RetrievalNotAuthorized, RetrievalService
from app.modules.tutor.entailment import GeminiEntailmentChecker
from app.modules.tutor.models import GroundingMode, TutorMessage
from app.modules.tutor.parsing import TutorParseError, parse_tutor_response
from app.modules.tutor.prompt import PROMPT_VERSION, SYSTEM_INSTRUCTION, ReferenceChunk, build_tutor_prompt
from app.modules.tutor.validation import ValidationStatus, validate_claims
from app.services.embedding.gateway import EmbeddingGateway
from app.services.generation.gateway import GenerationError, GenerationGateway
from app.services.vectorstore.store import VectorStore

TOP_N_CHUNKS = 6
INSUFFICIENT_EVIDENCE_TEXT = "Your uploaded materials don't cover this yet, so I can't answer it from your course content."


class TutorNotFound(Exception):
    """Course not found, or not owned by the caller."""


@dataclass
class CitationOut:
    claim: str
    chunk_id: str
    validation_status: str


@dataclass
class TutorResult:
    message_id: UUID
    grounding_mode: str
    answer_markdown: str
    citations: List[CitationOut]
    retrieved_chunk_ids: List[str]
    fallback_path: Optional[str]  # None | "stripped" | "retried" -- for audit/tests, not client-facing


class TutorService:
    def __init__(
        self,
        db: Session,
        generation: GenerationGateway,
        embeddings: EmbeddingGateway,
        vectors: VectorStore,
        cheap_generation: Optional[GenerationGateway] = None,
    ):
        self.db = db
        self.generation = generation
        self.retrieval = RetrievalService(db, embeddings, vectors)
        self.courses = CourseService(db)
        self.entailment_checker = GeminiEntailmentChecker(cheap_generation or generation)

    def ask(
        self,
        course_id: UUID,
        owner_id: int,
        question: str,
        context_lesson_id: Optional[UUID] = None,
        conversation_id: Optional[UUID] = None,
        decision_id: Optional[UUID] = None,
    ) -> TutorResult:
        try:
            self.courses.get_owned(course_id, owner_id)
        except CourseNotFound:
            raise TutorNotFound(str(course_id))

        try:
            hits = self.retrieval.search(course_id, owner_id, question, limit=TOP_N_CHUNKS)
        except RetrievalNotAuthorized:
            raise TutorNotFound(str(course_id))

        context_hint = None
        if context_lesson_id:
            lesson = self.db.query(Lesson).filter(Lesson.id == context_lesson_id).first()
            context_hint = lesson.title if lesson else None

        reference_chunks = [
            ReferenceChunk(chunk_id=str(h.id), text=h.text, heading_path=h.heading_path) for h in hits
        ]
        retrieved_chunk_ids = [str(h.id) for h in hits]
        chunk_text_by_id = {str(h.id): h.text for h in hits}
        user_prompt = build_tutor_prompt(question, reference_chunks, context_hint)

        if not hits:
            return self._finalize(
                course_id, owner_id, conversation_id, context_lesson_id, question,
                GroundingMode.INSUFFICIENT.value, INSUFFICIENT_EVIDENCE_TEXT, [], retrieved_chunk_ids, None,
                decision_id=decision_id,
            )

        try:
            raw = self.generation.generate(user_prompt, system_instruction=SYSTEM_INSTRUCTION)
            parsed = parse_tutor_response(raw)
        except (GenerationError, TutorParseError):
            return self._finalize(
                course_id, owner_id, conversation_id, context_lesson_id, question,
                GroundingMode.INSUFFICIENT.value, INSUFFICIENT_EVIDENCE_TEXT, [], retrieved_chunk_ids, "insufficiency",
                decision_id=decision_id,
            )

        if parsed.insufficient_evidence or not parsed.claims:
            return self._finalize(
                course_id, owner_id, conversation_id, context_lesson_id, question,
                GroundingMode.INSUFFICIENT.value if parsed.insufficient_evidence else GroundingMode.SOURCE_ONLY.value,
                parsed.answer_markdown or INSUFFICIENT_EVIDENCE_TEXT, [], retrieved_chunk_ids, None,
                decision_id=decision_id,
            )

        validated = validate_claims(self.db, parsed.claims, course_id, owner_id, chunk_text_by_id, self.entailment_checker)
        fallback_path = None
        final_answer = parsed.answer_markdown

        tier2_failures = [v for v in validated if v.tier1_passed and v.tier2_status == ValidationStatus.FAILED]
        if tier2_failures:
            fallback_path = "retried"
            try:
                retry_raw = self.generation.generate(user_prompt, system_instruction=SYSTEM_INSTRUCTION)
                retry_parsed = parse_tutor_response(retry_raw)
            except (GenerationError, TutorParseError):
                retry_parsed = None

            if retry_parsed and not retry_parsed.insufficient_evidence and retry_parsed.claims:
                retry_validated = validate_claims(
                    self.db, retry_parsed.claims, course_id, owner_id, chunk_text_by_id, self.entailment_checker
                )
                if all(v.tier1_passed and v.tier2_status != ValidationStatus.FAILED for v in retry_validated):
                    parsed = retry_parsed
                    validated = retry_validated
                    final_answer = retry_parsed.answer_markdown
                    fallback_path = "retried"
                else:
                    fallback_path = "stripped"
            else:
                fallback_path = "stripped"

        surviving = [v for v in validated if v.tier1_passed and v.tier2_status != ValidationStatus.FAILED]
        failing = [v for v in validated if not (v.tier1_passed and v.tier2_status != ValidationStatus.FAILED)]

        if failing:
            if fallback_path is None:
                fallback_path = "stripped"
            for v in failing:
                final_answer = final_answer.replace(v.claim.text, "")

        if not surviving and validated:
            # Every claim the answer depended on failed validation -- the
            # honest fallback, never a silently-stripped-to-nothing answer.
            return self._finalize(
                course_id, owner_id, conversation_id, context_lesson_id, question,
                GroundingMode.INSUFFICIENT.value, INSUFFICIENT_EVIDENCE_TEXT, [], retrieved_chunk_ids, "insufficiency",
                decision_id=decision_id,
            )

        citations = [
            CitationOut(claim=v.claim.text, chunk_id=v.claim.chunk_id, validation_status=v.tier2_status)
            for v in surviving
        ]
        return self._finalize(
            course_id, owner_id, conversation_id, context_lesson_id, question,
            GroundingMode.SOURCE_ONLY.value, final_answer.strip(), citations, retrieved_chunk_ids, fallback_path,
            decision_id=decision_id,
        )

    def generate_lesson_content(
        self, course_id: UUID, owner_id: int, lesson_id: UUID, format: str,
        decision_id: Optional[UUID] = None,
    ) -> TutorResult:
        """
        Real lesson content, not a placeholder: reuses the exact same
        retrieval -> grounded generation -> two-tier citation validation
        pipeline as ask() by phrasing the lesson's concepts as a question.
        This is a thin wrapper, not a parallel content-generation subsystem
        -- it inherits ask()'s ownership check, source-only default, and
        citation validation for free rather than duplicating any of it.
        """
        lesson = (
            self.db.query(Lesson)
            .join(Module, Lesson.module_id == Module.id)
            .join(CourseVersion, Module.course_version_id == CourseVersion.id)
            .filter(Lesson.id == lesson_id, CourseVersion.course_id == course_id)
            .first()
        )
        if lesson is None:
            raise TutorNotFound(str(lesson_id))

        concept_ids = [lc.concept_id for lc in lesson.concepts]
        concepts = self.db.query(Concept).filter(Concept.id.in_(concept_ids)).all() if concept_ids else []
        concept_names = ", ".join(c.name for c in concepts) or lesson.title

        format_label = format.replace("_", " ")
        query = (
            f'Write {format_label} instructional content teaching the following concepts, '
            f'in service of the lesson objective "{lesson.objective or lesson.title}": {concept_names}.'
        )
        return self.ask(course_id, owner_id, query, context_lesson_id=lesson_id, decision_id=decision_id)

    def _finalize(
        self, course_id, owner_id, conversation_id, context_lesson_id, question,
        grounding_mode, answer_markdown, citations: List[CitationOut], retrieved_chunk_ids, fallback_path,
        decision_id: Optional[UUID] = None,
    ) -> TutorResult:
        message = TutorMessage(
            owner_id=owner_id,
            course_id=course_id,
            decision_id=decision_id,
            conversation_id=conversation_id,
            context_lesson_id=context_lesson_id,
            question=question,
            answer_markdown=answer_markdown,
            retrieved_chunk_ids=retrieved_chunk_ids,
            citations=[{"claim": c.claim, "chunk_id": c.chunk_id, "validation_status": c.validation_status} for c in citations],
            grounding_mode=grounding_mode,
            model_id=self.generation.model_name,
            prompt_version=PROMPT_VERSION,
        )
        self.db.add(message)
        self.db.commit()
        self.db.refresh(message)
        return TutorResult(
            message_id=message.id, grounding_mode=grounding_mode, answer_markdown=answer_markdown,
            citations=citations, retrieved_chunk_ids=retrieved_chunk_ids, fallback_path=fallback_path,
        )
