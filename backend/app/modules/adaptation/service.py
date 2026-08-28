"""
AdaptationService: candidate generation (impure -- reads mastery/graph/
lesson state) around the pure scoring.recommend() core, plus the
AdaptationDecision persistence the mandate requires happen BEFORE any
recommendation is returned.

FIRST-INSPECT NOTE (mandate): neither app/core/archetypes.py (deleted, per
Phase 0's audit) nor app/services/adaptation.py (FSLSM presentation-style
code, used only by chat/router.py and content/router.py) is imported
anywhere in this module. Nothing here reads a fixed learner label.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple
from uuid import UUID

from sqlalchemy.orm import Session

from app.modules.adaptation.models import AdaptationDecision, PresentationAffinity
from app.modules.adaptation.policy import DEFAULT_POLICY
from app.modules.adaptation.presentation import AffinityState, apply_manual_switch, apply_outcome, select_format
from app.modules.adaptation.readiness import compute_readiness, is_ready
from app.modules.adaptation.scoring import Candidate, ConceptState, LearnerStateSnapshot, recommend
from app.modules.courses.service import CourseNotFound, CourseService
from app.modules.curriculum.models import ConceptPrerequisite, CourseVersion, EdgeStrength
from app.modules.curriculum.service import CurriculumService
from app.modules.mastery.models import MasteryEvent
from app.modules.mastery.service import MasteryService
from app.services.embedding.gateway import EmbeddingGateway
from app.services.generation.gateway import GenerationGateway

# Named, unvalidated defaults -- see candidates section of this module.
REMEDIATION_TRIGGER_MASTERY = 0.5
REMEDIATION_PREREQUISITE_THRESHOLD = 0.6
REMEDIATION_BONUS = 0.5  # a flat bump so a triggered remediation outranks ordinary candidates
STRUGGLING_RECENT_EVENTS = 5
STRUGGLING_CORRECTNESS_THRESHOLD = 0.4
TARGETED_PRACTICE_MAX_CANDIDATES = 3
CHALLENGE_MAX_CANDIDATES = 2

_DEFAULT_FORMAT = {
    "NEW_LESSON": "detailed",
    "PREREQUISITE_REMEDIATION": "worked_example",
    "TARGETED_PRACTICE": "concise",
    "CHALLENGE": "analogy",
    "RESUME_INTERRUPTED": "detailed",
}
_ESTIMATED_MINUTES = {
    "NEW_LESSON": 15.0,
    "PREREQUISITE_REMEDIATION": 10.0,
    "TARGETED_PRACTICE": 5.0,
    "CHALLENGE": 8.0,
    "RESUME_INTERRUPTED": 15.0,
}


class AdaptationNotFound(Exception):
    """Course not found, or not owned by the caller."""


class AdaptationPersistenceError(Exception):
    """The AdaptationDecision failed to persist -- no recommendation may be
    returned when this is raised."""


@dataclass
class Recommendation:
    decision_id: UUID
    recommended: dict
    alternatives: List[dict]


class AdaptationService:
    def __init__(self, db: Session, generation: GenerationGateway, embeddings: Optional[EmbeddingGateway] = None):
        self.db = db
        self.courses = CourseService(db)
        self.curriculum = CurriculumService(db, generation, embeddings)
        self.mastery = MasteryService(db, generation, embeddings)

    def _get_owned_course(self, course_id: UUID, owner_id: int):
        try:
            return self.courses.get_owned(course_id, owner_id)
        except CourseNotFound:
            raise AdaptationNotFound(str(course_id))

    # -- candidate generation (impure: reads already-fetched domain state) ----

    def _build_concept_states(self, concepts, edges, owner_id: int) -> Dict[UUID, ConceptState]:
        hard_prereqs: Dict[UUID, List[UUID]] = {}
        soft_prereqs: Dict[UUID, List[UUID]] = {}
        for edge in edges:
            bucket = hard_prereqs if edge.strength == EdgeStrength.HARD.value else soft_prereqs
            bucket.setdefault(edge.dependent_concept_id, []).append(edge.prerequisite_concept_id)

        raw_states = {c.id: self.mastery.get_concept_mastery(owner_id, c.id) for c in concepts}

        concept_states: Dict[UUID, ConceptState] = {}
        for concept in concepts:
            hard_masteries = [raw_states[p].mastery for p in hard_prereqs.get(concept.id, []) if p in raw_states]
            soft_masteries = [raw_states[p].mastery for p in soft_prereqs.get(concept.id, []) if p in raw_states]
            readiness = compute_readiness(hard_masteries, soft_masteries)
            state = raw_states[concept.id]
            concept_states[concept.id] = ConceptState(
                mastery=state.mastery, uncertainty=state.uncertainty,
                importance=concept.importance, readiness=readiness,
            )
        return concept_states, hard_prereqs

    def _last_decision(self, course_id: UUID, owner_id: int) -> Optional[AdaptationDecision]:
        return (
            self.db.query(AdaptationDecision)
            .filter(AdaptationDecision.course_id == course_id, AdaptationDecision.owner_id == owner_id)
            .order_by(AdaptationDecision.created_at.desc())
            .first()
        )

    def _rejected_candidate_keys(self, last_decision: Optional[AdaptationDecision]) -> Set[Tuple[str, tuple]]:
        """Every candidate the previous call considered but did not select --
        used to soften a candidate that keeps getting proposed and ignored."""
        if last_decision is None:
            return set()
        return {
            (entry["activity_type"], tuple(UUID(cid) for cid in entry["concept_ids"]))
            for entry in last_decision.candidates_considered
            if not entry.get("selected")
        }

    def _is_struggling(self, course_id: UUID, owner_id: int) -> bool:
        recent = (
            self.db.query(MasteryEvent)
            .filter(MasteryEvent.course_id == course_id, MasteryEvent.owner_id == owner_id)
            .order_by(MasteryEvent.created_at.desc())
            .limit(STRUGGLING_RECENT_EVENTS)
            .all()
        )
        if len(recent) < STRUGGLING_RECENT_EVENTS:
            return False
        return (sum(e.correctness for e in recent) / len(recent)) < STRUGGLING_CORRECTNESS_THRESHOLD

    def _affinity_states(self, owner_id: int) -> Dict[str, AffinityState]:
        rows = self.db.query(PresentationAffinity).filter(PresentationAffinity.owner_id == owner_id).all()
        return {
            row.format: AffinityState(
                exposure_count=row.exposure_count, success_count=row.success_count, effectiveness=row.effectiveness,
            )
            for row in rows
        }

    def _presentation_affinity_map(self, owner_id: int) -> Dict[str, float]:
        return {fmt: state.effectiveness for fmt, state in self._affinity_states(owner_id).items()}

    def _get_or_create_affinity_row(self, owner_id: int, format: str) -> PresentationAffinity:
        row = (
            self.db.query(PresentationAffinity)
            .filter(PresentationAffinity.owner_id == owner_id, PresentationAffinity.format == format)
            .first()
        )
        if row is None:
            row = PresentationAffinity(owner_id=owner_id, format=format)
            self.db.add(row)
            self.db.flush()
        return row

    def record_presentation_outcome(self, owner_id: int, format: str, success: bool) -> PresentationAffinity:
        """The next checkpoint outcome after a block was viewed in `format`
        feeds back in as evidence -- see presentation.py's EMA."""
        row = self._get_or_create_affinity_row(owner_id, format)
        updated = apply_outcome(
            AffinityState(exposure_count=row.exposure_count, success_count=row.success_count, effectiveness=row.effectiveness),
            success=success,
        )
        row.exposure_count = updated.exposure_count
        row.success_count = updated.success_count
        row.effectiveness = updated.effectiveness
        self.db.commit()
        self.db.refresh(row)
        return row

    def record_manual_switch(self, owner_id: int, from_format: str, to_format: str) -> None:
        """A learner's own override is itself just more evidence, never a
        contradiction to correct (guardrail)."""
        if from_format:
            row = self._get_or_create_affinity_row(owner_id, from_format)
            updated = apply_manual_switch(
                AffinityState(exposure_count=row.exposure_count, success_count=row.success_count, effectiveness=row.effectiveness),
                switched_toward=False,
            )
            row.effectiveness = updated.effectiveness
        to_row = self._get_or_create_affinity_row(owner_id, to_format)
        updated_to = apply_manual_switch(
            AffinityState(exposure_count=to_row.exposure_count, success_count=to_row.success_count, effectiveness=to_row.effectiveness),
            switched_toward=True,
        )
        to_row.effectiveness = updated_to.effectiveness
        self.db.commit()

    def _generate_candidates(
        self, version: CourseVersion, concept_states: Dict[UUID, ConceptState],
        hard_prereqs: Dict[UUID, List[UUID]], last_decision: Optional[AdaptationDecision],
        course_id: UUID, owner_id: int,
    ) -> List[Candidate]:
        candidates: List[Candidate] = []

        def make(activity_type, concept_ids, lesson_id=None, remediation_bonus=0.0, difficulty=0.5):
            return Candidate(
                activity_type=activity_type, concept_ids=tuple(concept_ids), lesson_id=lesson_id,
                estimated_minutes=_ESTIMATED_MINUTES[activity_type],
                activity_difficulty=difficulty, default_format=_DEFAULT_FORMAT[activity_type],
                remediation_bonus=remediation_bonus,
            )

        # -- B: PREREQUISITE_REMEDIATION -- concrete trigger from the mandate:
        # a checkpoint dropped concept X below 0.5 AND a hard prerequisite Y
        # of X sits below 0.6. `remediation_trigger_by_target` keeps Y -> X so
        # the reason text can name the actual concept that was missed, not
        # just the prerequisite being remediated.
        remediation_trigger_by_target: Dict[UUID, UUID] = {}
        for concept_id, state in concept_states.items():
            if state.mastery >= REMEDIATION_TRIGGER_MASTERY:
                continue
            for prereq_id in hard_prereqs.get(concept_id, []):
                prereq_state = concept_states.get(prereq_id)
                if prereq_state and prereq_state.mastery < REMEDIATION_PREREQUISITE_THRESHOLD:
                    remediation_trigger_by_target.setdefault(prereq_id, concept_id)
        for target_id in remediation_trigger_by_target:
            candidates.append(
                make("PREREQUISITE_REMEDIATION", [target_id], remediation_bonus=REMEDIATION_BONUS,
                     difficulty=1.0 - concept_states[target_id].mastery)
            )
        self._last_remediation_triggers = remediation_trigger_by_target

        # -- NEW_LESSON: first lesson, in course order, whose concepts are
        # all ready and not already fully mastered.
        for module in version.modules:
            for lesson in module.lessons:
                lesson_concept_ids = [lc.concept_id for lc in lesson.concepts]
                if not lesson_concept_ids:
                    continue
                states = [concept_states[cid] for cid in lesson_concept_ids if cid in concept_states]
                if not states:
                    continue
                if all(is_ready(s.readiness) for s in states) and any(s.mastery < 0.85 for s in states):
                    candidates.append(make("NEW_LESSON", lesson_concept_ids, lesson_id=lesson.id))
                    break
            else:
                continue
            break

        # -- RESUME_INTERRUPTED: last call recommended a NEW_LESSON and no
        # evidence has appeared for any of its concepts since.
        if last_decision is not None and last_decision.selected_activity_type == "NEW_LESSON":
            prior_concept_ids = last_decision.candidates_considered
            selected_entry = next(
                (e for e in prior_concept_ids if e.get("selected")), None
            )
            if selected_entry:
                concept_ids = [UUID(cid) for cid in selected_entry["concept_ids"]]
                has_new_evidence = (
                    self.db.query(MasteryEvent)
                    .filter(
                        MasteryEvent.course_id == course_id, MasteryEvent.owner_id == owner_id,
                        MasteryEvent.concept_id.in_(concept_ids),
                        MasteryEvent.created_at > last_decision.created_at,
                    )
                    .first()
                    is not None
                )
                if not has_new_evidence:
                    candidates.append(
                        make("RESUME_INTERRUPTED", concept_ids, lesson_id=last_decision.selected_lesson_id)
                    )

        # -- TARGETED_PRACTICE: concepts with evidence, ready, not yet mastered.
        practice_pool = sorted(
            (cid for cid, s in concept_states.items() if s.uncertainty < 1.0 and s.mastery < 0.85 and is_ready(s.readiness)),
            key=lambda cid: concept_states[cid].mastery,
        )
        for concept_id in practice_pool[:TARGETED_PRACTICE_MAX_CANDIDATES]:
            candidates.append(make("TARGETED_PRACTICE", [concept_id]))

        # -- CHALLENGE: concepts already mastered.
        mastered_pool = [cid for cid, s in concept_states.items() if s.mastery >= 0.85 and s.uncertainty <= 0.35]
        for concept_id in mastered_pool[:CHALLENGE_MAX_CANDIDATES]:
            candidates.append(make("CHALLENGE", [concept_id], difficulty=0.8))

        return candidates

    # -- the main entry point ------------------------------------------------

    def recommend_next(self, course_id: UUID, owner_id: int) -> Recommendation:
        self._get_owned_course(course_id, owner_id)
        graph = self.curriculum.get_graph(course_id, owner_id)
        if not graph.concepts:
            raise AdaptationNotFound(f"No course structure generated for {course_id}")

        version = self.db.query(CourseVersion).filter(CourseVersion.id == graph.concepts[0].course_version_id).first()
        concept_states, hard_prereqs = self._build_concept_states(graph.concepts, graph.edges, owner_id)
        last_decision = self._last_decision(course_id, owner_id)
        rejected_keys = self._rejected_candidate_keys(last_decision)

        candidates = self._generate_candidates(
            version, concept_states, hard_prereqs, last_decision, course_id, owner_id
        )
        if not candidates:
            raise AdaptationNotFound(f"No eligible activity for course {course_id}")

        course = self._get_owned_course(course_id, owner_id)
        snapshot = LearnerStateSnapshot(
            concepts=concept_states,
            presentation_affinity=self._presentation_affinity_map(owner_id),
            goal_text=course.goal,
            rejected_candidate_keys=rejected_keys,
        )
        ranked = recommend(candidates, snapshot, DEFAULT_POLICY)
        winner = ranked[0]

        affinity_states = self._affinity_states(owner_id)
        is_struggling = self._is_struggling(course_id, owner_id)
        exposure_index = sum(s.exposure_count for s in affinity_states.values())
        presentation_format = select_format(affinity_states, exposure_index, is_struggling)

        concept_names = {c.id: c.name for c in graph.concepts}
        triggers = getattr(self, "_last_remediation_triggers", {})
        reason = self._reason_text(winner.candidate, concept_names, triggers)

        candidates_considered = [
            {
                "activity_type": sc.candidate.activity_type,
                "concept_ids": [str(cid) for cid in sc.candidate.concept_ids],
                "lesson_id": str(sc.candidate.lesson_id) if sc.candidate.lesson_id else None,
                "score": sc.score,
                "features": sc.features,
                "selected": sc is winner,
            }
            for sc in ranked
        ]

        decision = AdaptationDecision(
            owner_id=owner_id,
            course_id=course_id,
            selected_activity_type=winner.candidate.activity_type,
            selected_concept_id=winner.candidate.concept_ids[0] if winner.candidate.concept_ids else None,
            selected_lesson_id=winner.candidate.lesson_id,
            reason_text=reason,
            candidates_considered=candidates_considered,
            policy_version=DEFAULT_POLICY.version,
            input_snapshot={
                "concept_mastery": {str(cid): s.mastery for cid, s in concept_states.items()},
                "concept_uncertainty": {str(cid): s.uncertainty for cid, s in concept_states.items()},
            },
        )
        self.db.add(decision)
        try:
            self.db.commit()
        except Exception as exc:
            self.db.rollback()
            raise AdaptationPersistenceError(str(exc)) from exc
        self.db.refresh(decision)

        def render(sc, include_format=False):
            out = {
                "activity_type": sc.candidate.activity_type,
                "concept_ids": [str(cid) for cid in sc.candidate.concept_ids],
                "lesson_id": str(sc.candidate.lesson_id) if sc.candidate.lesson_id else None,
                "reason": self._reason_text(sc.candidate, concept_names, triggers),
                "score": sc.score,
            }
            if include_format:
                out["presentation_format"] = presentation_format
            return out

        return Recommendation(
            decision_id=decision.id,
            recommended=render(winner, include_format=True),
            alternatives=[render(sc) for sc in ranked[1:]],
        )

    @staticmethod
    def _reason_text(
        candidate: Candidate, concept_names: Dict[UUID, str], remediation_triggers: Dict[UUID, UUID]
    ) -> str:
        names = [concept_names.get(cid, str(cid)) for cid in candidate.concept_ids]
        joined = ", ".join(names) if names else "this material"
        if candidate.activity_type == "PREREQUISITE_REMEDIATION":
            target_id = candidate.concept_ids[0] if candidate.concept_ids else None
            trigger_id = remediation_triggers.get(target_id)
            trigger_name = concept_names.get(trigger_id, "a recent check") if trigger_id else "a recent check"
            return (
                f"Recommended because {joined} needs review before continuing -- "
                f"{trigger_name} dropped below a solid level on a recent check, "
                f"and it depends on {joined}."
            )
        if candidate.activity_type == "NEW_LESSON":
            return f"You're ready for the next lesson, covering {joined}."
        if candidate.activity_type == "TARGETED_PRACTICE":
            return f"Extra practice on {joined} to strengthen a developing area."
        if candidate.activity_type == "CHALLENGE":
            return f"A challenge on {joined}, since you've already mastered it."
        if candidate.activity_type == "RESUME_INTERRUPTED":
            return f"Pick back up where you left off, on {joined}."
        return f"Recommended: {joined}."
