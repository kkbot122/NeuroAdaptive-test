# P4 adaptive activity contract

Status: implemented locally on `feat/p4-adaptive-activities`, based on the coherent
P3 integration commit `552f3e8`. Hosted behavior is not verified. The additive
migration is `d3f4a8c1e620`; it extends P2 preparation records without adding a second
activity, assessment, progress, or mastery store.

## Approved, unvalidated policies

- **Remediation evidence:** select a concept for remediation only when that concept's
  own evidence is `Needs attention` and `More supporting evidence`. P3's existing
  `mastery-v1` and `evidence-strength-v1` labels supply those signals. `Not assessed`,
  `Limited evidence`, and `Developing` evidence lead to practice. A missing source does
  not imply weakness. A repeated remediation remains eligible only while its evidence
  still qualifies.
- **Challenge eligibility:** require recorded completed teaching and `Proficient` or
  `Mastered` evidence. Select two eligible concepts when possible; otherwise use a
  clearly labeled single-concept application. This changes eligibility/targets only;
  scoring weights and deterministic ordering remain unchanged.
- **Challenge attribution:** each question has exactly one evidence concept among the
  selected taught concepts, and the set covers every selected concept. Grounding
  validation rejects a question that cannot isolate that concept. A wrong answer
  therefore updates only its attributed concept.
- **Question counts:** the defaults are five fresh MCQs per P4 activity type:
  `P4_REMEDIATION_QUESTION_COUNT_V1`,
  `P4_TARGETED_PRACTICE_QUESTION_COUNT_V1`, and
  `P4_CHALLENGE_QUESTION_COUNT_V1`. They are named, versioned, configurable, and
  unvalidated defaults. The P2 maximum remains eight.

## Learner experiences

| Type | Selection and screen | Preparation and assessment |
| --- | --- | --- |
| Prerequisite remediation | One concept with its factual evidence reason; no unrelated lesson is selected. | Prepare a supported objective, explanation, worked example, recap, and fresh MCQs. Learner studies and marks reading complete before questions. Missing support is a recoverable limitation. Each new remediation has a fresh question set and a different supported explanation when available. |
| Targeted practice | One previously taught concept with `Not assessed`, `Limited evidence`, or `Developing` evidence and a factual reason. | Prepare fresh MCQs directly; no additional lesson or reading step is required at launch. |
| Challenge | Completed teaching plus proficient/mastered evidence. Show two concepts where eligible; otherwise clearly call it a single-concept application. | Prepare questions directly, with no reading gate. For a pair, questions apply only the selected taught concepts in a less familiar current-course situation. Each item isolates and records one of those concepts. |

All experiences persist their selected decision ID, type, targets, reason, course version,
format, and P1 fixed assessment set. Existing P1 answer/resume and P3 result/progress/
Continue paths remain authoritative. Feedback stays hidden until set submission; pending
grading is unresolved. Continue resumes unfinished work first and then uses the existing
selection path. Diagnostic resume and `NEW_LESSON`/`RESUME_INTERRUPTED` still use their
existing behavior.

## Selection, preparation, and API

- Candidate construction reuses P3 evidence labels and completed teaching records. It
  only changes P4 eligibility and target construction. The selected reason and trace
  snapshot retain evidence bands, strength, effective evidence weight, and taught
  concept IDs. Practice requires completed teaching at every evidence level. Covered
  lessons are excluded from `NEW_LESSON`, and each eligible uncovered lesson remains a
  candidate in course order. No mastery formula, decay, recommendation weight, or
  tie-break changes.
- P4 remediation reads source chunks explicitly associated with its one target concept
  in the current course/version. It does not invent prerequisite content or perform
  linked-course retrieval (P8). When none support teaching, preparation reports an
  honest recoverable failure.
- Preparation and reusable artifact identity include activity purpose and sorted target
  concepts. Remediation includes the activity identity so another run can vary its
  supported explanation. Question-only practice/challenge prepare questions directly.
  P2 typed schemas, ownership checks, source provenance, complete support checks,
  immutable question versions, freshness checks, retries, caching, leases, and atomic AI
  accounting remain in use. Compatible migrated P2/P3 READY preparations are adopted
  by exact activity, purpose, target, format, and version checks. Legacy P2 lesson
  artifacts are reused only when their original key, target set, version, format,
  validation versions, source fingerprint, and citations match; old keys are not rewritten.
- Existing routes drive the lifecycle: `POST /courses/{course_id}/activities/next`,
  `GET /courses/{course_id}/activities/{activity_id}`, the existing preparation retry
  and content routes, reading completion for remediation only, and the existing
  assessment/session routes. `LearningActivityOut` now includes `question_count`; P4
  remediation content permits a null `lesson_id`. OpenAPI and generated TypeScript were
  regenerated together. Foreign/mismatched resources remain owner/course scoped.
- The new `/courses/{courseId}/activities/{activityId}` screen renders remediation,
  targeted practice, and challenge as distinct states; P2 lesson study and existing
  session/result screens are reused where appropriate. Tutor assistance remains
  unavailable during assessment. If question generation fails after remediation
  teaching is saved, the recovery action remains visible beside the explanation.

## Persistence and verification

Migration `d3f4a8c1e620` makes preparation/artifact lesson links nullable, adds
`activity_purpose` and `target_concept_ids`, and backfills existing P2 rows. Its downgrade
refuses to discard lessonless question-only records. Account deletion removes new P4
content, preparation, question, source, and attribution rows through the existing records.

Local checks on this branch:

- `cd backend && /private/tmp/neurolearn-p1-venv/bin/pytest -o addopts= --disable-warnings -q`
  — 683 passed, 18 skipped, 1 warning (PostgreSQL integration tests are opt-in and
  were run separately below).
- `cd backend && /private/tmp/neurolearn-p1-venv/bin/ruff check app tests` — passed.
  `cd backend && /private/tmp/neurolearn-p1-venv/bin/alembic heads` — one head,
  `d3f4a8c1e620`.
- Disposable PostgreSQL integration:
  `P1_TEST_DATABASE_URL=postgresql://postgres:password@127.0.0.1:5433/neurolearn_p4_check_20261008_adaptive /private/tmp/neurolearn-p1-venv/bin/pytest -o addopts= --disable-warnings -q tests/integration/test_learning_postgresql.py`
  — 6 passed, 1 warning. The disposable database was dropped after the run.
- `cd frontend && PATH=/private/tmp/neurolearn-p1-venv/bin:$PATH npm run contracts:check`
  — passed. `npm test` — 7 passed; `npm run lint` and `npm run typecheck` — passed.
  `npm run build` with placeholder backend/auth configuration — passed.
- Deterministic API/service tests cover the P4 recommendation, preparation, fixed
  assessment, feedback, attribution, retry, deletion, and lesson → weak result →
  remediation → reassessment → progress → Continue cycle. `git diff --check` — passed.
- Review regressions cover retry visibility when remediation teaching is already saved,
  covered-lesson exclusion with later ready lessons retained, limited/developing practice
  eligibility before and after teaching, and adoption of READY P2 preparations/artifacts
  without redundant enqueue or teaching generation.
- Test providers are deterministic. No automated check spends live AI quota. The
  existing Docker stack was reused; no hosted runtime or production migration was used.

## Limits and P5 handoff

MCQs are implemented; short-answer grading, rubric/reviewer policy, reviewer corrections,
and durable grading-worker recovery remain P5 work. The evidence labels and counts are
not calibrated to learner outcomes. Side panels and the full workspace redesign remain
P6. Linked-course retrieval remains P8. Local checks do not prove hosted provider,
storage, worker, or concurrency behavior.

P5 can rely on the existing fixed-session answer/grading lifecycle, attributed P4 MCQs,
source-backed feedback, immutable attempts/evidence, P3 before/after progress, and
Continue. It should extend those paths without adding parallel assessment or mastery
records.
