# P2 grounded preparation contract

Status: implemented locally on `feat/p2-grounded-preparation`; not deployed. Migration
`b2e7c19a4d63_async_grounded_preparation` follows P1 `a61c9e7d4b20`. FastAPI
OpenAPI is authoritative; `backend/openapi.json` and
`frontend/lib/generated/api.ts` are generated from it.

## Approved defaults

These named, configurable, versioned defaults are approved for P2, but have not been
empirically validated. They do not change mastery thresholds, evidence decay, or
recommendation scoring.

| Policy | Config | Default and behavior |
| --- | --- | --- |
| Lesson assessment size | `P2_ASSESSMENT_DEFAULT_QUESTION_COUNT_V1`, `P2_ASSESSMENT_MAX_QUESTION_COUNT_V1` | 5 MCQs; raise to cover every taught concept; cap at 8 and expose recoverable unavailability above the bound |
| MCQ evidence | `P2_MCQ_DEFAULT_DIFFICULTY_V1` | Binary correct/incorrect; difficulty 0.5 |
| Fresh questions | `p2-question-freshness-v1` | Reject normalized duplicate prompts within the course version; diagnostic questions are never assigned |
| Candidate/retry bound | `P2_PREPARATION_MAX_CANDIDATES_V1` | Three candidates per failed stage, then learner-triggered retry resumes at that stage |
| Lookahead | `P2_PREPARATION_MAX_LOOKAHEAD_V1` | At most one next lesson, content only, after first activity readiness and at lower Celery priority |
| Grounding context bound | `P2_PREPARATION_MAX_SOURCE_CHUNKS_V1` | At most 12 current-course source chunks per preparation |

## Preparation and publication

Ingestion and curriculum services remain the upload, extraction, source-indexing,
outline-validation, and publication path. `POST /courses/{course_id}/publish-structure`
queues the first eligible lesson and MCQ set after the validated version is activated.
The preparation is durable and initially has no activity ID; when P1 selects the matching
first lesson, the existing row and its artifacts are attached to that activity. Navigation
still creates or resumes preparation as a recovery path. The learner can open validated
saved lesson text as soon as the content stage finishes. No other lessons or formats block
first-activity readiness.

PostgreSQL stores preparation status, stage, progress, errors, retry/candidate/provider
call counts, lease/dispatch timestamps, and artifact keys. Celery delivery is
deduplicated by durable publication-first, activity, and version/format keys, with
student work at priority 0 and the optional lookahead at priority 9. Workers lock and
refresh the preparation row before artifact/question writes and completion/failure state
changes; a stale lease token or expired lease cannot commit. Each AI request reserves its
daily allowance through an atomic owner/date increment. Lease expiry permits a pending
task to be redispatched. A retry resumes at content or questions based on saved artifacts
and question membership. Published-version, source checksum, curriculum fingerprint,
owner/course, and chunk/document ownership checks prevent stale or foreign artifacts
from being served. Existing account deletion removes preparation rows, content and
citation records, question memberships, and question-source relations before their
referenced courses, activities, questions, and chunks.

The default format is prepared first. Other formats are requested through the same
content endpoint and have their own durable preparation; validated matching artifacts
are reused across activities in the same published version. The API never performs
provider generation inline. Pending/running content returns HTTP 202; saved content or
a recoverable failure returns HTTP 200 with its separate readiness fields.

## Content and MCQs

Lesson output is parsed through strict Pydantic schemas. Objective, explanation,
example, and recap are arrays of complete display statements. Every statement must
cover a lesson concept and cite current-course chunks owned by the learner. Every
factual statement is checked by the existing tiered claim validator; unsupported
statements are removed, and any section or concept left inadequately covered rejects
the candidate. Abstention text is never stored as content. Saved artifacts preserve
the source and curriculum fingerprints, course version, presentation format, chunk
citations, generator/validator model IDs, prompt/schema/validation-policy versions,
validation state, and timestamp.

The MCQ draft schema requires four distinct options, exactly one option matching the
correct answer, an explanation, one taught concept, and chunk provenance. The source
must support the question stem, correct answer, and explanation; each distractor is
checked not to be another supported correct answer. A failed or malformed entailment
response is distinct from a successful unsupported result: P2 retries the candidate and
never treats an unavailable check as proof that a distractor is false. Invalid candidates
are rejected.
Only after a whole valid set is ready are questions created through `MasteryService`
as immutable, versioned questions with model, prompt/schema/validation metadata and
source relations. `PreparedActivityQuestion` binds IDs, versions, and order to one
preparation. P1 then snapshots those references into its fixed assessment session.

## API and learner behavior

All routes retain the P1 `/api/v1` authentication and owner/course checks; foreign
resources return 404.

| Method and path | P2 behavior |
| --- | --- |
| `POST /courses/{course_id}/activities/next` | Select/resume through P1 and queue/resume preparation |
| `GET /courses/{course_id}/activities/{activity_id}` | Return saved activity plus preparation state and queue/resume if needed |
| `GET /courses/{course_id}/activities/{activity_id}/content?format=...` | Return saved validated content, or 202 while requested content is pending |
| `POST /courses/{course_id}/activities/{activity_id}/preparation/retry?format=...` | Resume the failed stage or requested variant |
| `POST /courses/{course_id}/activities/{activity_id}/assessment` | Reject until the complete prepared question set is ready, then use P1 fixed-session behavior |

Before set submission, the P1 contract omits expected answers, correctness,
explanations, and source feedback. Submitted feedback includes the supported answer,
explanation, and source references. Reading completion only records coverage; graded
answers continue through the existing one-attempt/one-evidence lifecycle.

The study and assessment pages show preparation/retry states, load saved text, display
source links, and allow the existing P1 assessment flow. They do not implement the
larger redesigned course UI.

## Local demonstration and verification

The deterministic API flow in
`backend/tests/api/test_preparation.py::test_uploaded_outline_publish_saved_lesson_and_fixed_mcq_assessment`
uploads an unfamiliar text source, runs the existing extraction/outline path, publishes
the validated outline, confirms first preparation is queued before any activity exists,
runs deterministic worker generation/validation, selects the first activity and confirms
it adopts the prepared row, reads the saved lesson without another generation call,
completes reading without mastery change, starts the fixed MCQ set,
resumes saved answers, and sees explanations/citations only after submission. It does
not call live Gemini.

Checks executed locally on 2026-10-08:

- Backend full suite: `/private/tmp/neurolearn-p1-venv/bin/pytest -o addopts= --disable-warnings -q` — **655 passed, 17 skipped, 1 warning**.
- Focused preparation, entailment, SQLite migration, abuse-budget, and publication tests: `/private/tmp/neurolearn-p1-venv/bin/pytest -o addopts= --disable-warnings -q tests/api/test_preparation.py tests/integration/test_preparation_migration.py tests/security/test_t5_abuse_dos_cost.py tests/api/test_curriculum.py tests/api/test_baseline_boundaries.py` — **52 passed, 1 warning**.
- P2 account-deletion regression: `/private/tmp/neurolearn-p1-venv/bin/pytest -o addopts= --disable-warnings -q tests/security/test_privacy_and_audit.py::TestAccountDeletion::test_deletion_removes_p2_preparation_content_and_question_provenance` — **1 passed, 1 warning**; also included in the full suite above.
- Disposable PostgreSQL migration/uniqueness/concurrency and atomic quota reservations: `P1_TEST_DATABASE_URL=postgresql://postgres:password@127.0.0.1:5433/neurolearn_p2_check_20261008_final /private/tmp/neurolearn-p1-venv/bin/pytest -o addopts= --disable-warnings -q tests/integration/test_learning_postgresql.py` — **5 passed, 1 warning**. The disposable database was dropped afterward; the persistent Compose database was not migrated.
- Backend lint: `/private/tmp/neurolearn-p1-venv/bin/ruff check app tests` — passed. Migration graph: `/private/tmp/neurolearn-p1-venv/bin/alembic heads` — one head, `b2e7c19a4d63`.
- Contracts: `PATH=/private/tmp/neurolearn-p1-venv/bin:$PATH npm run contracts:check` — passed; generated `backend/openapi.json` includes HTTP 202 and frontend types match.
- Frontend: `npm test` — 6 passed; `npm run lint`, `npm run typecheck`, and `npm run build` — passed. The build used placeholder backend/auth values. Typecheck was run after the build had generated Next.js route types.
- `git diff --check` — passed.

No live Gemini check, hosted service verification, production migration, deployment, or
production data change was performed. The single Python warning is the existing
Starlette `BlockingPortal` deprecation warning.

## Limits and P3 handoff

P2 grounds only in the current course; explicitly linked earlier courses remain P8.
At the P2 baseline, the distinct P4 activity set was not implemented. P4 now extends the
preparation seam; see the [P4 contract](p4-adaptive-activities-contract.md). There is
still no short-answer grading/review worker, full UI redesign, live-provider validation,
or hosted worker verification. Question-generation failure preserves saved content and
leaves assessment unavailable/recoverable; it creates no question set or mastery
evidence.

P3 relies on P1 activity/session/answer/evidence lifecycle, P2 durable preparation
stages and validated saved lesson artifacts, activity-bound immutable question IDs/
versions/order, exact set-submission feedback gating, and zero mastery effect from
reading. Implemented P3 results/progress/next-activity behavior is in
[p3-results-progress-contract.md](p3-results-progress-contract.md); it adds no second
progress or assessment system.
