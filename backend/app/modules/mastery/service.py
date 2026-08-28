"""
MasteryService: question authoring, attempt grading, and per-concept mastery
reporting. Ownership is resolved through CourseService.get_owned (the same
single accessor every other module uses), so a course that does not exist or
belongs to someone else is indistinguishable -- both raise MasteryNotFound,
which the router renders as 404 (courses/service.py's own stated rationale,
reused here rather than re-invented).
"""
from datetime import datetime, timezone
from typing import Dict, List, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.modules.courses.service import CourseNotFound, CourseService
from app.modules.curriculum.service import CurriculumService
from app.modules.mastery import diagnostic, engine
from app.modules.mastery.grading import grade_attempt
from app.modules.mastery.models import MasteryEvent, Question, QuestionAttempt, QuestionConcept
from app.services.embedding.gateway import EmbeddingGateway
from app.services.generation.gateway import GenerationGateway


class MasteryNotFound(Exception):
    """Course, question, or attempt not found, or not owned by the caller."""


class InvalidQuestionWeights(Exception):
    """A multi-concept question's QuestionConcept weights must sum to 1."""


_WEIGHT_SUM_TOLERANCE = 1e-6


class MasteryService:
    def __init__(self, db: Session, generation: GenerationGateway, embeddings: Optional[EmbeddingGateway] = None):
        self.db = db
        self.generation = generation
        self.courses = CourseService(db)
        # CurriculumService is reused only for its ownership-scoped graph
        # read (concepts + edges for the relevant course version) -- mastery
        # never calls anything that generates or mutates curriculum content.
        self.curriculum = CurriculumService(db, generation, embeddings)

    def _get_owned_course(self, course_id: UUID, owner_id: int):
        try:
            return self.courses.get_owned(course_id, owner_id)
        except CourseNotFound:
            raise MasteryNotFound(str(course_id))

    # -- question authoring --------------------------------------------------

    def create_question(
        self,
        course_id: UUID,
        course_version_id: UUID,
        owner_id: int,
        question_type: str,
        prompt: str,
        concept_weights: Dict[UUID, float],
        options: Optional[List[str]] = None,
        correct_answer=None,
        rubric: Optional[List[str]] = None,
        difficulty: float = 0.5,
        is_diagnostic: bool = False,
        prompt_version: str = diagnostic.DIAGNOSTIC_PROMPT_VERSION,
        decision_id: Optional[UUID] = None,
    ) -> Question:
        if not concept_weights:
            raise InvalidQuestionWeights("A question must map to at least one concept.")
        total_weight = sum(concept_weights.values())
        if abs(total_weight - 1.0) > _WEIGHT_SUM_TOLERANCE:
            raise InvalidQuestionWeights(
                f"QuestionConcept weights must sum to 1, got {total_weight}."
            )

        question = Question(
            course_id=course_id,
            course_version_id=course_version_id,
            owner_id=owner_id,
            question_type=question_type,
            prompt=prompt,
            options=options,
            correct_answer=correct_answer,
            rubric=rubric,
            difficulty=difficulty,
            is_diagnostic=1 if is_diagnostic else 0,
            version=1,
            # Reproducibility (Phase 7): required, not optional -- every
            # Question row must be traceable to what generated it.
            model_id=self.generation.model_name,
            prompt_version=prompt_version,
            decision_id=decision_id,
        )
        self.db.add(question)
        self.db.flush()
        for concept_id, weight in concept_weights.items():
            self.db.add(QuestionConcept(question_id=question.id, concept_id=concept_id, weight=weight))
        self.db.commit()
        self.db.refresh(question)
        return question

    def supersede_question(self, question_id: UUID, owner_id: int, **updated_fields) -> Question:
        """Never mutates the old row -- creates version n+1 with
        supersedes_question_id set. Historical QuestionAttempt rows keep
        pointing at the old question_id/question_version untouched."""
        old = (
            self.db.query(Question)
            .filter(Question.id == question_id, Question.owner_id == owner_id)
            .first()
        )
        if old is None:
            raise MasteryNotFound(str(question_id))

        old_weights = {
            qc.concept_id: qc.weight
            for qc in self.db.query(QuestionConcept).filter(QuestionConcept.question_id == old.id).all()
        }
        new_question = Question(
            course_id=old.course_id,
            course_version_id=old.course_version_id,
            owner_id=owner_id,
            question_type=updated_fields.get("question_type", old.question_type),
            prompt=updated_fields.get("prompt", old.prompt),
            options=updated_fields.get("options", old.options),
            correct_answer=updated_fields.get("correct_answer", old.correct_answer),
            rubric=updated_fields.get("rubric", old.rubric),
            difficulty=updated_fields.get("difficulty", old.difficulty),
            is_diagnostic=old.is_diagnostic,
            version=old.version + 1,
            supersedes_question_id=old.id,
            # Regenerated now, by whichever gateway is current -- not
            # copied from the row being superseded.
            model_id=self.generation.model_name,
            prompt_version=updated_fields.get("prompt_version", old.prompt_version),
            decision_id=old.decision_id,
        )
        self.db.add(new_question)
        self.db.flush()
        for concept_id, weight in old_weights.items():
            self.db.add(QuestionConcept(question_id=new_question.id, concept_id=concept_id, weight=weight))
        self.db.commit()
        self.db.refresh(new_question)
        return new_question

    # -- diagnostic ------------------------------------------------------------

    def generate_diagnostic(
        self, course_id: UUID, owner_id: int, max_questions: Optional[int] = None
    ) -> List[Question]:
        self._get_owned_course(course_id, owner_id)
        graph = self.curriculum.get_graph(course_id, owner_id)
        if not graph.concepts:
            return []

        version_id = graph.concepts[0].course_version_id
        sampled = diagnostic.sample_concepts_for_diagnostic(graph.concepts, graph.edges, max_questions)
        drafts = diagnostic.generate_diagnostic_questions(sampled, self.generation)

        questions: List[Question] = []
        for draft in drafts:
            question = self.create_question(
                course_id=course_id,
                course_version_id=version_id,
                owner_id=owner_id,
                question_type="MCQ",
                prompt=draft.prompt,
                concept_weights={draft.concept_id: 1.0},
                options=draft.options,
                correct_answer=draft.correct_answer,
                difficulty=draft.difficulty,
                is_diagnostic=True,
            )
            questions.append(question)
        return questions

    # -- attempts ---------------------------------------------------------------

    def submit_attempt(
        self,
        question_id: UUID,
        owner_id: int,
        given_answer,
        hints_used: int = 0,
        retry_index: int = 0,
        time_taken_seconds: Optional[float] = None,
        confidence: Optional[float] = None,
    ) -> QuestionAttempt:
        question = (
            self.db.query(Question)
            .filter(Question.id == question_id, Question.owner_id == owner_id)
            .first()
        )
        if question is None:
            raise MasteryNotFound(str(question_id))

        correctness = grade_attempt(question, given_answer, self.generation)

        attempt = QuestionAttempt(
            question_id=question.id,
            question_version=question.version,
            owner_id=owner_id,
            course_id=question.course_id,
            given_answer=given_answer,
            correctness=correctness,
            time_taken_seconds=time_taken_seconds,
            hints_used=hints_used,
            retry_index=retry_index,
            self_reported_confidence=confidence,
        )
        self.db.add(attempt)
        self.db.flush()

        concept_links = (
            self.db.query(QuestionConcept).filter(QuestionConcept.question_id == question.id).all()
        )
        for link in concept_links:
            weight = engine.evidence_weight_base(
                concept_weight=link.weight,
                difficulty=question.difficulty,
                hints_used=hints_used,
                retry_index=retry_index,
            )
            self.db.add(
                MasteryEvent(
                    owner_id=owner_id,
                    concept_id=link.concept_id,
                    course_id=question.course_id,
                    course_version_id=question.course_version_id,
                    question_attempt_id=attempt.id,
                    correctness=correctness,
                    evidence_weight_base=weight,
                )
            )

        self.db.commit()
        self.db.refresh(attempt)
        return attempt

    # -- reporting ----------------------------------------------------------------

    def get_concept_mastery(self, owner_id: int, concept_id: UUID) -> engine.MasteryState:
        """Independent per (owner_id, concept_id) -- reads only this
        concept's evidence, never another concept's."""
        rows = (
            self.db.query(MasteryEvent)
            .filter(MasteryEvent.owner_id == owner_id, MasteryEvent.concept_id == concept_id)
            .all()
        )
        events = [
            engine.EvidenceEvent(
                correctness=r.correctness,
                evidence_weight_base=r.evidence_weight_base,
                # SQLite drops tzinfo on round-trip even for a timezone=True
                # column; every timestamp this app writes is UTC (server
                # default `func.now()`), so a naive value read back is
                # re-attached as UTC rather than compared naively.
                created_at=r.created_at if r.created_at.tzinfo else r.created_at.replace(tzinfo=timezone.utc),
            )
            for r in rows
        ]
        return engine.compute_mastery(events, datetime.now(timezone.utc))

    def get_mastery_report(self, course_id: UUID, owner_id: int, include_raw: bool = False) -> List[dict]:
        """One row per concept in the relevant course version (active, else
        latest generated -- same fallback CurriculumService.get_graph uses).
        Qualitative band is the primary field; raw mastery/uncertainty are
        only attached, under a separate `raw` key, when explicitly asked."""
        self._get_owned_course(course_id, owner_id)
        graph = self.curriculum.get_graph(course_id, owner_id)

        report = []
        for concept in graph.concepts:
            state = self.get_concept_mastery(owner_id, concept.id)
            row = {
                "concept_id": str(concept.id),
                "concept_name": concept.name,
                "band": engine.classify_band(state),
            }
            if include_raw:
                row["raw"] = {
                    "mastery": state.mastery,
                    "uncertainty": state.uncertainty,
                    "evidence_weight_total": state.evidence_weight_total,
                }
            report.append(row)
        return report
