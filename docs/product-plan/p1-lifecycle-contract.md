# P1 activity and assessment lifecycle

Status: implemented locally on `feat/p1-learning-state`; this records the
repository contract and executed checks, not a deployed service. Migration:
`a61c9e7d4b20` (after the P0 head `83c6e2f41b85`). FastAPI OpenAPI and
`frontend/lib/generated/api.ts` are the API/type authorities.

## Records and lifecycle

| Record | Durable facts |
| --- | --- |
| `LearningActivity` | Owner, course/version, optional recommendation decision, type, target concepts, optional lesson, selected presentation, reading position/completion, activity state |
| `AssessmentSession` | Activity, course version, diagnostic/activity kind, open/submitted state and submission time |
| `AssessmentQuestion` | Fixed question ID, question version, and zero-based order within the session |
| `AnswerSubmission` | One confirmed answer per session question and grading status/failure |
| `QuestionAttempt` / `MasteryEvent` | Existing graded-attempt and concept-evidence records; P1 links an attempt to its assessment question for deduplication |

One unfinished activity is allowed per owner/course. `POST activities/next`
returns that activity when present; otherwise it persists the selected
recommendation decision and activity together. Diagnostic sessions may have no
recommendation decision. Activity assessment assignment uses already persisted
questions owned by the same course version and target concepts. P1 does not
generate lesson-scoped questions.

Activity states are `SELECTED`, `PREPARING`, `READY`, `IN_PROGRESS`,
`AWAITING_ASSESSMENT`, `AWAITING_GRADING`, `COMPLETED`, and
`RECOVERABLE_FAILURE`. Current synchronous paths create `READY` activities or
`IN_PROGRESS` diagnostics. Saving position/format moves `SELECTED`/`READY` to
`IN_PROGRESS`; reading completion records coverage and moves a lesson activity
to `AWAITING_ASSESSMENT`; starting its question set moves it to `IN_PROGRESS`.
Lesson-bound assessment sets require server-recorded reading completion before
creation and submission; activities without a lesson can start directly.
Submitting a fully answered set moves it to `COMPLETED` once lesson coverage is
recorded and every answer is graded, or `AWAITING_GRADING` while any grading
remains unresolved. A failed grading attempt remains retryable. `PREPARING` and `RECOVERABLE_FAILURE` are
reserved for asynchronous preparation/recovery work and are not emitted by the
current P1 synchronous activity selector.

Assessment submission is `OPEN` until every fixed question has a persisted
answer, then `SUBMITTED`. Answer states are `AWAITING_GRADING`, `GRADING_FAILED`,
and `GRADED`. Session grading state is derived as `NOT_STARTED`,
`AWAITING_GRADING`, `RETRY_REQUIRED`, or `COMPLETE`. Correctness, expected
answers, and rubrics are absent until session submission. After submission,
available results are returned; an unresolved grade has null correctness and
keeps grading pending. A confirmed answer is immutable; identical transport
retries return the saved session, while conflicting answers return 409.
Successful grades create the existing attempt/evidence records once, but
mastery reports and recommendation inputs exclude fixed-session evidence until
the full set is submitted. Prior evidence remains visible during the session.

Reading completion contributes only to lesson coverage. `learning-state`
returns covered lesson IDs/count separately from the existing concept mastery
report. A submitted assessment can complete with weak or incorrect results;
that status does not claim mastery. Existing mastery calculations remain
unchanged and are not calibrated by P1.

Because grading commits separately from assessment submission, completion is
reconciled after reacquiring and refreshing the owner/course-scoped session row
lock. Submission and grading therefore serialize the final activity transition;
reads/resume repair a process-stop gap, and retrying an already graded answer
can also repair a stale activity state.

## HTTP contract

All routes are under `/api/v1`, require existing authenticated identity, and
scope resource reads and writes by owner and course. A foreign/mismatched
resource returns 404. Invalid lifecycle transitions/conflicting answer writes
return 409. Pydantic forbids undeclared input properties for progress and
answer bodies.

| Method and path | Input | Result |
| --- | --- | --- |
| `POST /courses/{course_id}/activities/next` | none | Existing or newly persisted `LearningActivityOut` |
| `GET /courses/{course_id}/activities/{activity_id}` | none | Saved activity and optional assessment session ID |
| `PATCH /courses/{course_id}/activities/{activity_id}` | `{ "reading_position"?: integer >= 0, "presentation_format"?: supported format }` | Updated activity |
| `POST /courses/{course_id}/activities/{activity_id}/reading-complete` | none | Activity with reading completion timestamp |
| `POST /courses/{course_id}/activities/{activity_id}/assessment` | none | Existing or newly fixed `AssessmentSessionOut` |
| `POST /courses/{course_id}/diagnostic` | `{ "max_questions"?: integer (1–50) }` | Resumed or newly saved diagnostic session; does not require a decision |
| `GET /courses/{course_id}/assessment-sessions/{session_id}` | none | Same saved question IDs, versions, order, answers, and permitted feedback |
| `POST /courses/{course_id}/assessment-sessions/{session_id}/questions/{question_id}/answer` | `{ "given_answer": value }` | Saved session with answer/grading status |
| `POST /courses/{course_id}/assessment-sessions/{session_id}/questions/{question_id}/retry-grading` | none | Session after retry, with unchanged saved answer |
| `POST /courses/{course_id}/assessment-sessions/{session_id}/submit` | none | Submitted session and any available results |
| `GET /courses/{course_id}/learning-state` | none | Active activity, lesson coverage, and concept understanding |

`AssessmentSessionOut` contains session/activity IDs, assessment type,
submission state, derived grading state, submission timestamp, and ordered
questions. Each question includes its ID/version/position, prompt/type/options,
optional saved answer/status, and an optional result. Clients never send the
question set, ownership, correctness, expected answers, or rubrics. The legacy
`POST /courses/{course_id}/diagnostic` response changed from a question array
to this session response. The legacy standalone attempt endpoint returns 404
for questions assigned to a fixed session so it cannot bypass set submission.

## P2 integration and later limits

P2 now creates activity/version-scoped persisted MCQs, then uses this lifecycle to
snapshot their immutable IDs, versions, and order. It prepares content asynchronously
through the existing activity states and preserves owner/course/version checks and the
answer → grading → evidence transaction boundary. P3 can rely on idempotent answer
records and the existing `MasteryService.record_graded_attempt` path for one
attempt/evidence write per assessment question. See
[P2 grounded preparation](p2-grounded-preparation-contract.md) for defaults, API,
migration, tests, and limits.

P2 does not add recommendation scoring, short-answer AI grading workers, linked
courses, calibrated mastery/completion policies, or a redesigned learning UI.
Short-answer grading can fail and persist `GRADING_FAILED`; durable worker-based retries
belong to a later milestone. Deployment, live provider behavior, and production
migration remain unverified and untouched.

## Local verification executed

Commands ran from `backend/` or `frontend/` as indicated on 2026-10-07. Checks
use deterministic fakes and disposable SQLite/PostgreSQL schemas; none spend
live AI quota.

- Backend full suite: `/private/tmp/neurolearn-p1-venv/bin/pytest -q` — passed; tests carrying existing skip markers and opt-in PostgreSQL checks were skipped without `P1_TEST_DATABASE_URL`.
- Lifecycle/migration focus: `/private/tmp/neurolearn-p1-venv/bin/pytest -q tests/api/test_learning_lifecycle.py tests/integration/test_learning_migration.py tests/integration/test_learning_postgresql.py` — passed; PostgreSQL-only tests skipped without `P1_TEST_DATABASE_URL`.
- PostgreSQL disposable checks: `P1_TEST_DATABASE_URL=postgresql://postgres:password@127.0.0.1:5433/neurolearn_p1_check_20261007_a61c9 /private/tmp/neurolearn-p1-venv/bin/pytest -q tests/integration/test_learning_postgresql.py` — 2 passed against a throwaway database on the local Docker PostgreSQL service. The tests performed a real migration upgrade/downgrade and a paused grading/submission interleave; the activity completed with one answer and one mastery evidence event.
- Backend lint: `/private/tmp/neurolearn-p1-venv/bin/ruff check app tests` — passed.
- Migration graph: `/private/tmp/neurolearn-p1-venv/bin/alembic heads` — one head, `a61c9e7d4b20`.
- Contracts: `PATH=/private/tmp/neurolearn-p1-venv/bin:$PATH npm run contracts:check` — passed; generated OpenAPI and TypeScript agree.
- Frontend: `npm test` (6/6), `npm run lint`, `npm run typecheck`, and `npm run build` — passed. Build used placeholder backend/auth environment values.
- Diff hygiene: `git diff --check` — passed.

The migration also compiles under PostgreSQL's 63-character identifier limit.
The user's persistent Compose `neuro_db` was not migrated by these checks; the
throwaway database was dropped afterward. No production migration, application
restart, deployment, or user-data deletion was run. Assessment Back and
pending-results exit actions now go to the dashboard so `/learn` can remain
the resume/continue path.
