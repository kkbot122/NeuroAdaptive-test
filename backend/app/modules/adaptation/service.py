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
from app.modules.curriculum.models import ConceptPrerequisite, CourseVersion, EdgeStrength, Lesson, Module
from app.modules.curriculum.service import CurriculumService
from app.modules.learning.models import LearningActivity
from app.modules.mastery import engine
from app.modules.mastery.models import MasteryEvent
from app.modules.mastery.service import MasteryService
from app.services.embedding.gateway import EmbeddingGateway
from app.services.generation.gateway import GenerationGateway

# Named, unvalidated defaults -- see candidates section of this module.
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
                evidence_weight_total=state.evidence_weight_total,
            )
        return concept_states, hard_prereqs

    @staticmethod
    def _mastery_state(state: ConceptState) -> engine.MasteryState:
        return engine.MasteryState(
            mastery=state.mastery,
            uncertainty=state.uncertainty,
            evidence_weight_total=state.evidence_weight_total,
        )

    def _taught_concepts(self, course_id: UUID, version_id: UUID, owner_id: int, concept_ids: Set[UUID]) -> Set[UUID]:
        if not concept_ids:
            return set()
        rows = (
            self.db.query(LearningActivity.target_concept_ids)
            .filter(
                LearningActivity.owner_id == owner_id,
                LearningActivity.course_id == course_id,
                LearningActivity.course_version_id == version_id,
                LearningActivity.activity_type.in_(
                    {"NEW_LESSON", "RESUME_INTERRUPTED", "PREREQUISITE_REMEDIATION"}
                ),
                LearningActivity.reading_completed_at.isnot(None),
            )
            .all()
        )
        taught: Set[UUID] = set()
        for (target_ids,) in rows:
            for target_id in target_ids or []:
                parsed = UUID(target_id) if isinstance(target_id, str) else target_id
                if parsed in concept_ids:
                    taught.add(parsed)
        return taught

    def _covered_lessons(self, course_id: UUID, version_id: UUID, owner_id: int) -> Set[UUID]:
        return {
            lesson_id
            for (lesson_id,) in self.db.query(LearningActivity.lesson_id)
            .filter(
                LearningActivity.owner_id == owner_id,
                LearningActivity.course_id == course_id,
                LearningActivity.course_version_id == version_id,
                LearningActivity.lesson_id.isnot(None),
                LearningActivity.reading_completed_at.isnot(None),
            )
            .all()
        }

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
            self.mastery.visible_mastery_events()
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
        course_id: UUID, owner_id: int, concepts,
    ) -> List[Candidate]:
        candidates: List[Candidate] = []

        def make(activity_type, concept_ids, lesson_id=None, remediation_bonus=0.0, difficulty=0.5):
            return Candidate(
                activity_type=activity_type, concept_ids=tuple(concept_ids), lesson_id=lesson_id,
                estimated_minutes=_ESTIMATED_MINUTES[activity_type],
                activity_difficulty=difficulty, default_format=_DEFAULT_FORMAT[activity_type],
                remediation_bonus=remediation_bonus,
            )

        # Remediation needs supported evidence on the selected concept itself.
        # An unassessed prerequisite cannot inherit weakness from a dependent.
        remediation_targets = [
            concept_id
            for concept_id, state in concept_states.items()
            if engine.classify_band(self._mastery_state(state)) == engine.NEEDS_ATTENTION
            and engine.classify_evidence_strength(self._mastery_state(state))
            == engine.MORE_SUPPORTING_EVIDENCE
        ]
        for target_id in remediation_targets:
            candidates.append(
                make(
                    "PREREQUISITE_REMEDIATION",
                    [target_id],
                    remediation_bonus=REMEDIATION_BONUS,
                     difficulty=1.0 - concept_states[target_id].mastery)
            )

        # -- NEW_LESSON: each uncovered lesson whose concepts are all ready
        # and not already fully mastered. Covered lessons are reinforcement,
        # handled by practice, remediation, or challenge candidates.
        covered_lesson_ids = self._covered_lessons(course_id, version.id, owner_id)
        for module in version.modules:
            for lesson in module.lessons:
                if lesson.id in covered_lesson_ids:
                    continue
                lesson_concept_ids = [lc.concept_id for lc in lesson.concepts]
                if not lesson_concept_ids:
                    continue
                states = [concept_states[cid] for cid in lesson_concept_ids if cid in concept_states]
                if not states:
                    continue
                if all(is_ready(s.readiness) for s in states) and any(s.mastery < 0.85 for s in states):
                    candidates.append(make("NEW_LESSON", lesson_concept_ids, lesson_id=lesson.id))

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
                    self.mastery.visible_mastery_events()
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

        concept_ids = set(concept_states)
        taught = self._taught_concepts(course_id, version.id, owner_id, concept_ids)

        # -- TARGETED_PRACTICE: no evidence, limited evidence, or developing
        # evidence. A never-taught, unassessed concept stays with new lessons.
        practice_pool = []
        for cid, state in concept_states.items():
            mastery_state = self._mastery_state(state)
            strength = engine.classify_evidence_strength(mastery_state)
            band = engine.classify_band(mastery_state)
            if strength not in {engine.NOT_ASSESSED, engine.LIMITED_EVIDENCE} and band != engine.DEVELOPING:
                continue
            if cid not in taught:
                continue
            if strength != engine.NOT_ASSESSED and not is_ready(state.readiness):
                continue
            practice_pool.append(cid)
        practice_pool.sort(key=lambda cid: concept_states[cid].mastery)
        for concept_id in practice_pool[:TARGETED_PRACTICE_MAX_CANDIDATES]:
            candidates.append(make("TARGETED_PRACTICE", [concept_id]))

        # -- CHALLENGE: concepts with completed teaching and demonstrated
        # Proficient/Mastered evidence. Pair selected concepts when possible;
        # retain a single-concept application when only one qualifies.
        ordered_concepts = [concept.id for concept in concepts]
        challenge_pool = [
            cid
            for cid in ordered_concepts
            if cid in taught
            and engine.classify_band(self._mastery_state(concept_states[cid]))
            in {engine.PROFICIENT, engine.MASTERED}
        ]
        challenge_pairs = []
        for index, first in enumerate(challenge_pool):
            for second in challenge_pool[index + 1 :]:
                challenge_pairs.append((first, second))
                if len(challenge_pairs) >= CHALLENGE_MAX_CANDIDATES:
                    break
            if len(challenge_pairs) >= CHALLENGE_MAX_CANDIDATES:
                break
        selected_challenges = challenge_pairs[:CHALLENGE_MAX_CANDIDATES]
        if not selected_challenges:
            selected_challenges = [(cid,) for cid in challenge_pool[:CHALLENGE_MAX_CANDIDATES]]
        for concept_ids in selected_challenges:
            candidates.append(make("CHALLENGE", concept_ids, difficulty=0.8))

        return candidates

    # -- the main entry point ------------------------------------------------

    def recommend_next(self, course_id: UUID, owner_id: int, *, persist: bool = True) -> Recommendation:
        self._get_owned_course(course_id, owner_id)
        graph = self.curriculum.get_graph(course_id, owner_id)
        if not graph.concepts:
            raise AdaptationNotFound(f"No course structure generated for {course_id}")

        version = self.db.query(CourseVersion).filter(CourseVersion.id == graph.concepts[0].course_version_id).first()
        concept_states, hard_prereqs = self._build_concept_states(graph.concepts, graph.edges, owner_id)
        last_decision = self._last_decision(course_id, owner_id)
        rejected_keys = self._rejected_candidate_keys(last_decision)

        candidates = self._generate_candidates(
            version, concept_states, hard_prereqs, last_decision, course_id, owner_id, graph.concepts
        )
        if not candidates:
            raise AdaptationNotFound(f"No eligible activity for course {course_id}")
        taught_concepts = self._taught_concepts(course_id, version.id, owner_id, set(concept_states))

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
        reason = self._reason_text(winner.candidate, concept_names, concept_states)

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
                "concept_evidence_weight_total": {
                    str(cid): s.evidence_weight_total for cid, s in concept_states.items()
                },
                "concept_evidence_strength": {
                    str(cid): engine.classify_evidence_strength(self._mastery_state(s))
                    for cid, s in concept_states.items()
                },
                "concept_understanding_band": {
                    str(cid): engine.classify_band(self._mastery_state(s))
                    for cid, s in concept_states.items()
                },
                "completed_teaching_concept_ids": sorted(str(item) for item in taught_concepts),
            },
        )
        self.db.add(decision)
        if persist:
            try:
                self.db.commit()
            except Exception as exc:
                self.db.rollback()
                raise AdaptationPersistenceError(str(exc)) from exc
            self.db.refresh(decision)
        else:
            # LearningActivityService writes this decision in the same
            # transaction as the durable activity it selected.
            self.db.flush()

        def render(sc, include_format=False):
            out = {
                "activity_type": sc.candidate.activity_type,
                "concept_ids": [str(cid) for cid in sc.candidate.concept_ids],
                "lesson_id": str(sc.candidate.lesson_id) if sc.candidate.lesson_id else None,
                "reason": self._reason_text(sc.candidate, concept_names, concept_states),
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
        candidate: Candidate,
        concept_names: Dict[UUID, str],
        concept_states: Dict[UUID, ConceptState],
    ) -> str:
        names = [concept_names.get(cid, str(cid)) for cid in candidate.concept_ids]
        joined = ", ".join(names) if names else "this material"
        if candidate.activity_type == "PREREQUISITE_REMEDIATION":
            return (
                f"Submitted assessment evidence places {joined} in Needs attention with more supporting evidence, "
                "so this activity revisits that concept."
            )
        if candidate.activity_type == "NEW_LESSON":
            return f"You're ready for the next lesson, covering {joined}."
        if candidate.activity_type == "TARGETED_PRACTICE":
            state = concept_states[candidate.concept_ids[0]]
            mastery_state = AdaptationService._mastery_state(state)
            strength = engine.classify_evidence_strength(mastery_state)
            band = engine.classify_band(mastery_state)
            if strength == engine.NOT_ASSESSED:
                return f"{joined} is not assessed yet; a short practice check can establish a baseline."
            if strength == engine.LIMITED_EVIDENCE:
                return f"There is limited submitted evidence for {joined}; a short practice check can clarify what you know."
            return f"Submitted evidence places {joined} in Developing; targeted practice can help show what is secure."
        if candidate.activity_type == "CHALLENGE":
            if len(candidate.concept_ids) == 1:
                label = engine.classify_band(
                    AdaptationService._mastery_state(concept_states[candidate.concept_ids[0]])
                )
                return (
                    f"You completed teaching for {joined} and your submitted evidence is {label}; "
                    "try a single-concept application in a new situation."
                )
            labels = [
                f"{concept_names.get(cid, str(cid))} ({engine.classify_band(AdaptationService._mastery_state(concept_states[cid]))})"
                for cid in candidate.concept_ids
            ]
            return (
                f"You completed teaching for {', '.join(concept_names.get(cid, str(cid)) for cid in candidate.concept_ids)} "
                f"with submitted evidence at {', '.join(labels)}; apply both concepts in a new situation."
            )
        if candidate.activity_type == "RESUME_INTERRUPTED":
            return f"Pick back up where you left off, on {joined}."
        return f"Recommended: {joined}."
