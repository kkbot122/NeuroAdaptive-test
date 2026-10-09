"""Conservative, source-backed matching for a single authorized course link.

Names and stored embeddings are never treated as equivalence. A reliable
match needs identical normalized definitions and each version's own source
passage must contain that full definition statement. A same-name candidate
with overlapping definitions stays uncertain; a same-name candidate with
different definition vocabulary is unsupported. This deterministic rule is
intentionally narrow: paraphrases are not accepted as proof of equivalence.
"""
import re
from uuid import UUID

from sqlalchemy.orm import Session

from app.modules.courses.models import Course
from app.modules.courses.p8_models import CrossCourseConceptMatch
from app.modules.curriculum.models import Concept, ConceptSource, CourseVersion
from app.modules.documents.chunk_models import Chunk

_TOKEN = re.compile(r"\w+|[^\w\s]", re.UNICODE)
_STOP = {"a", "an", "and", "are", "as", "at", "by", "for", "from", "in", "into", "is", "of", "on", "or", "the", "to", "with"}
_CONFLICT_CUE = re.compile(
    r"\b(?:not|never|false|incorrect|wrong|contrary|contradict\w*|"
    r"den(?:y|ies|ied)|cannot|can\s*['’]?\s*t|couldn\s*['’]?\s*t|"
    r"wouldn\s*['’]?\s*t|shouldn\s*['’]?\s*t|mustn\s*['’]?\s*t|"
    r"mightn\s*['’]?\s*t|hasn\s*['’]?\s*t|haven\s*['’]?\s*t|"
    r"hadn\s*['’]?\s*t|doesn\s*['’]?\s*t|don\s*['’]?\s*t|"
    r"didn\s*['’]?\s*t|isn\s*['’]?\s*t|aren\s*['’]?\s*t|"
    r"wasn\s*['’]?\s*t|weren\s*['’]?\s*t|shan\s*['’]?\s*t|"
    r"instead|except|unless|however)\b"
)


def _normalized(text: str) -> str:
    """Normalize case and whitespace while retaining every non-word symbol.

    Keeping punctuation and Unicode symbols is intentionally conservative:
    comparison operators, signs, decimal points, and grouping marks cannot
    disappear and make otherwise different definitions look equivalent.
    """
    return " ".join(_TOKEN.findall((text or "").casefold()))


def _meaning_words(text: str) -> set[str]:
    return {
        token
        for token in _TOKEN.findall((text or "").casefold())
        if token[0].isalnum() and token not in _STOP
    }


def _supported(definition: str, passages: list[str]) -> bool:
    phrase = _normalized(definition)
    if not phrase:
        return False
    positive_support = False
    for passage in passages:
        sentences = re.split(r"(?<=[.!?;])\s+|\n+", passage)
        for sentence in sentences:
            padded_passage = f" {_normalized(sentence)} "
            padded_phrase = f" {phrase} "
            start = 0
            while (position := padded_passage.find(padded_phrase, start)) >= 0:
                context_start = max(0, position - 96)
                context_end = min(len(padded_passage), position + len(padded_phrase) + 64)
                context = padded_passage[context_start:context_end]
                if _CONFLICT_CUE.search(context):
                    # Conflicting source context means the exact phrase is not
                    # adequate affirmative support for this version.
                    return False
                positive_support = True
                start = position + len(padded_phrase)
    return positive_support


def _has_conflicting_context(passages: list[str]) -> bool:
    """Fail closed when a concept's source set contains qualification cues."""
    return any(_CONFLICT_CUE.search(_normalized(passage)) for passage in passages)


def _concepts(db: Session, course_id: UUID, version_id: UUID, owner_id: int) -> list[Concept]:
    return db.query(Concept).filter_by(
        course_id=course_id, course_version_id=version_id, owner_id=owner_id
    ).order_by(Concept.id).all()


def _passages(db: Session, concept_ids: list[UUID], course_id: UUID, owner_id: int) -> dict[UUID, list[str]]:
    if not concept_ids:
        return {}
    rows = (
        db.query(ConceptSource.concept_id, Chunk.text)
        .join(Chunk, Chunk.id == ConceptSource.chunk_id)
        .filter(
            ConceptSource.concept_id.in_(concept_ids),
            ConceptSource.course_id == course_id,
            ConceptSource.owner_id == owner_id,
            Chunk.course_id == course_id,
            Chunk.owner_id == owner_id,
        ).all()
    )
    result: dict[UUID, list[str]] = {}
    for concept_id, text in rows:
        result.setdefault(concept_id, []).append(text)
    return result


def build_link_matches(db: Session, course: Course, current_version: CourseVersion) -> list[CrossCourseConceptMatch]:
    """Persist the pinned, one-hop comparison when the current course publishes."""
    if course.linked_course_id is None or course.linked_version_id is None:
        return []
    linked = db.query(Course).filter_by(
        id=course.linked_course_id, owner_id=course.owner_id,
        status="PUBLISHED",
    ).first()
    linked_version = db.query(CourseVersion).filter_by(
        id=course.linked_version_id, course_id=course.linked_course_id,
        owner_id=course.owner_id, status="READY",
    ).first()
    if linked is None or linked_version is None:
        return []

    db.query(CrossCourseConceptMatch).filter_by(
        current_course_id=course.id, current_version_id=current_version.id
    ).delete(synchronize_session=False)
    current = _concepts(db, course.id, current_version.id, course.owner_id)
    earlier = _concepts(db, linked.id, linked_version.id, course.owner_id)
    current_sources = _passages(db, [item.id for item in current], course.id, course.owner_id)
    earlier_sources = _passages(db, [item.id for item in earlier], linked.id, course.owner_id)
    created = []

    candidates = []
    current_candidate_counts: dict[UUID, int] = {}
    linked_candidate_counts: dict[UUID, int] = {}
    for now in current:
        now_name = _normalized(" ".join([now.name, *(now.aliases or [])]))
        now_definition = _normalized(now.definition)
        for before in earlier:
            before_name = _normalized(" ".join([before.name, *(before.aliases or [])]))
            before_definition = _normalized(before.definition)
            if now_definition != before_definition and now_name != before_name:
                continue
            candidates.append((now, before, now_name == before_name, now_definition == before_definition))
            current_candidate_counts[now.id] = current_candidate_counts.get(now.id, 0) + 1
            linked_candidate_counts[before.id] = linked_candidate_counts.get(before.id, 0) + 1

    for now, before, same_name, same_definition in candidates:
        now_words = _meaning_words(now.definition)
        before_words = _meaning_words(before.definition)
        left_passages = current_sources.get(now.id, [])
        right_passages = earlier_sources.get(before.id, [])
        source_support = bool(
            left_passages and right_passages
            and _supported(now.definition, left_passages)
            and _supported(before.definition, right_passages)
        )
        conflicting_context = (
            _has_conflicting_context(left_passages)
            or _has_conflicting_context(right_passages)
        )
        unambiguous = current_candidate_counts[now.id] == 1 and linked_candidate_counts[before.id] == 1
        if not source_support:
            status = "UNSUPPORTED"
        elif same_definition:
            status = "RELIABLE" if unambiguous and not conflicting_context else "UNCERTAIN"
        elif same_name and now_words & before_words:
            status = "UNCERTAIN"
        else:
            status = "UNSUPPORTED"
        if status == "RELIABLE":
            rationale = "Definitions match and both version-specific source sets support them."
        elif status == "UNCERTAIN" and same_definition and not unambiguous:
            rationale = "Definitions and sources are supported, but the candidate mapping is ambiguous."
        elif status == "UNCERTAIN" and same_definition:
            rationale = "Definitions match and sources support them, but the source set has qualification cues."
        elif status == "UNCERTAIN":
            rationale = "The names and some definition terms align, but equivalence remains unproven."
        elif not source_support:
            rationale = "Both source sets do not support the definitions."
        else:
            rationale = "The concepts have different learning scope."
        match = CrossCourseConceptMatch(
            owner_id=course.owner_id,
            current_course_id=course.id,
            current_version_id=current_version.id,
            current_concept_id=now.id,
            linked_course_id=linked.id,
            linked_version_id=linked_version.id,
            linked_concept_id=before.id,
            status=status,
            provenance={
                "validator_version": "p8-definition-and-source-v3",
                "status_rationale": rationale,
                "source_support": source_support,
                "current_definition": now.definition,
                "linked_definition": before.definition,
                "current_source_chunk_ids": [str(cid) for cid in _source_chunk_ids(db, now.id, course.id, course.owner_id)],
                "linked_source_chunk_ids": [str(cid) for cid in _source_chunk_ids(db, before.id, linked.id, course.owner_id)],
            },
        )
        db.add(match)
        created.append(match)
    db.flush()
    return created


def _source_chunk_ids(db: Session, concept_id: UUID, course_id: UUID, owner_id: int) -> list[UUID]:
    return [row[0] for row in db.query(ConceptSource.chunk_id).filter_by(
        concept_id=concept_id, course_id=course_id, owner_id=owner_id
    ).order_by(ConceptSource.chunk_id).all()]
