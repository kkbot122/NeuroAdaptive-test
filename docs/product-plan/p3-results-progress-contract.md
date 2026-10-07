# P3 results, progress, and next activity

Status: implemented locally on `feat/p3-results-progress`, based on integration
commit `95e6171` (merged P2 fixes). Hosted behavior is not verified. No migration
was needed; P3 reuses the P1/P2 assessment, evidence, activity, decision, and
preparation records.

## Approved display policy

These are uncalibrated evidence labels, not estimates proven against learner
outcomes. P3 reuses the existing `mastery-v1` calculation and classifier:

| Display label | Rule |
| --- | --- |
| Not assessed | No visible graded evidence |
| Needs attention | Mastery estimate below 0.40 |
| Developing | Estimate from 0.40 to below 0.70 |
| Proficient | Estimate from 0.70 to below the Mastered gate |
| Mastered | Estimate at least 0.85 and uncertainty at most 0.35 |

P3 does not display the raw mastery percentage. The separate evidence-strength
policy is `evidence-strength-v1` with a fixed `1.0` boundary: no evidence is Not
assessed; recency-adjusted effective evidence weight below the boundary is Limited
evidence; weight at or above it is More supporting evidence. The boundary is a
versioned presentation default, not a tunable confidence score. A policy change
requires a new policy version. This describes support volume, not statistical
confidence. Neither display policy changes evidence weights, decay, mastery
calculation, or recommendation scoring.

Before/after uses `AssessmentSession.submitted_at` as the reference time for both
states. Before excludes this session's evidence. After adds only its successfully
graded evidence. A later successful grading retry is included at that same reference
time; unresolved answers contribute no evidence. Evidence from later unrelated
activity is excluded. The comparison is reproducible from existing immutable
`MasteryEvent` rows, question-attempt/session links, the saved reference time, and the
versioned existing mastery engine; no second mastery calculation is stored. Age decay
alone therefore cannot appear as an assessment-induced change.

## Implemented contract

- Fixed session questions retain saved ID, version, and order. Before whole-set
  submission, result feedback remains omitted. After submission, P2 feedback includes
  saved answers, correctness when grading succeeded, expected answer/rubric,
  explanation, and source chunk IDs. Pending/failed grading has no correctness and
  remains retryable against the unchanged answer.
- Session responses expose `graded_answer_count`, `unresolved_answer_count`,
  `concept_progress_reference_at`, and per-assessed-concept before/after bands and
  evidence strength. Only concepts linked to this fixed question set appear.
- `GET /courses/{course_id}/learning-state` includes the same separate evidence
  strength on concept rows. Reading completion still changes lesson coverage only.
- Submission, grading retry, and read/resume reconcile activity completion through
  the existing session/activity locks. An already graded answer returns its existing
  attempt/evidence. Pending answers are unresolved, never incorrect. A submitted
  comparison reads evidence and its linked session attribution in one SQL statement,
  keeping a grade committed during a PostgreSQL READ COMMITTED request on the correct
  side of the before/after comparison.
- Continue first resumes any unfinished activity/session, including saved diagnostics
  that do not use lesson preparation. Once the active activity
  is complete, `POST /courses/{course_id}/activities/next` uses the existing ranking
  path and persists the decision and activity together. Existing course locking and
  the unique unfinished-activity index protect concurrent Continue requests. The saved
  activity retains decision ID, course version, concept targets, factual reason, and
  presentation format.
- Results refresh uses only saved-session and learning-state reads; it does not select
  another activity or request generation. Continue is an explicit learner action.
  P2 preparation is requested/reused only for `NEW_LESSON` and `RESUME_INTERRUPTED`.
- API activity output labels other selected activity types `UNAVAILABLE` with the P4
  dependency. P3 does not launch an unsupported assessment or substitute another
  activity, including when a legacy session is attached. The learn route shows the
  recorded selection/reason and a direct dashboard exit.
- Results show lesson coverage separately from concept evidence and state that labels
  do not prove real-world mastery or learning gain. The current dashboard is the exit;
  the full course overview/workspace remains P6.

## API and persistence

OpenAPI remains authoritative. Generated `frontend/lib/generated/api.ts` includes:

- `MasteryReportRow.evidence_strength`;
- submitted-session grade counts, comparison reference time, and concept progress;
- `LearningActivityOut.experience_availability` and optional `unavailable_reason`.

No new records or columns were required. Existing account deletion already removes
activities, sessions, answers, attempts, and their evidence. There is no P3 migration;
`alembic heads` reports the existing head `b2e7c19a4d63`.

## P2 implementation inspected

The merged P2 code was inspected at the actual integration commit. Publication queues
first lesson/assessment preparation before an activity exists, and selection adopts
matching saved artifacts. Worker writes require the current unexpired lease token and
refresh heartbeats. Lesson statements and MCQs validate concept/source ownership and
claim support before persistence. Daily AI allowance uses an insert-on-conflict plus a
guarded atomic update. P3 did not change these paths or add live provider calls.

## Verification and limits

Executed local verification:

- `cd backend && /private/tmp/neurolearn-p1-venv/bin/pytest -o addopts= --disable-warnings -q` — 662 passed, 17 skipped, 1 warning. Deterministic fakes cover feedback gating, saved question versions and source feedback, pending grades, idempotent retry, fixed-time concept attribution under an interleaved grade commit, diagnostic Continue resume, reading coverage separation, unsupported P4 activities, concurrent Continue, and the full lesson → results → next lesson → preparation reuse → resume path.
- `cd backend && /private/tmp/neurolearn-p1-venv/bin/ruff check app tests` — passed.
- `cd backend && P1_TEST_DATABASE_URL=postgresql://postgres:password@127.0.0.1:5433/neurolearn_p3_check_20261008_results /private/tmp/neurolearn-p1-venv/bin/pytest -o addopts= --disable-warnings -q tests/integration/test_learning_postgresql.py` — 5 passed, 1 warning. The disposable database was dropped after the check.
- `cd frontend && PATH=/private/tmp/neurolearn-p1-venv/bin:$PATH npm run contracts:generate` — regenerated OpenAPI from FastAPI and frontend types.
- `cd frontend && PATH=/private/tmp/neurolearn-p1-venv/bin:$PATH npm run contracts:check` — passed.
- `cd frontend && npm test` — 6 passed; `cd frontend && npm run lint` and `cd frontend && npm run typecheck` — passed.
- `cd frontend && INTERNAL_API_URL=http://127.0.0.1:8000 INTERNAL_API_KEY=build-only-placeholder GOOGLE_CLIENT_ID=build-only-placeholder GOOGLE_CLIENT_SECRET=build-only-placeholder npm run build` — passed with placeholder service/auth values.
- `git diff --check` — passed.

The existing local Docker application and disposable PostgreSQL were used for local
checks; hosted behavior was not verified. No live AI quota, production data, migration,
deployment, or service purchase was used. PostgreSQL emitted an existing local
collation-version warning that did not prevent the tests from running.

P4 can rely on the persisted selected activity type/targets/reason/format, the
explicit availability state, fixed assessment/evidence lifecycle, and existing P2
lesson preparation seam. P4 must implement distinct remediation, targeted-practice,
and challenge content/questions; it must not reinterpret P3's unavailable response
as a lesson.
