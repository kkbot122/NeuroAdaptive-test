# P5 grounded short-answer and grading-review contract

Status: implemented locally on `feat/p5-short-answer-review`. The product defaults
below were approved for implementation. They are named, versioned, configurable,
unvalidated defaults; no hosted behavior or learning gain is claimed.

## Approved defaults

| Setting | Default | Behavior |
| --- | ---: | --- |
| `P5_SHORT_ANSWER_COUNT_V1` | 1 | Replace one MCQ in each newly prepared lesson, remediation, practice, and challenge set. Set sizes stay at five by default; diagnostics remain MCQ-only. |
| `P5_RUBRIC_CRITERIA_COUNT_V1` | 3 | Equally weighted, source-supported criteria per new short answer. |
| `P5_RUBRIC_PASSING_CRITERIA_V1` | 2 | At least two met criteria map to binary correct evidence; otherwise evidence is incorrect. Rubric count is feedback, not a fractional mastery score. |
| `P5_GRADING_MAX_PROVIDER_CALLS_V1` | 3 | Maximum same-provider grading calls per saved answer. No provider fallback. |
| `P5_GRADING_DISPATCH_COOLDOWN_SECONDS_V1` | 30 | Bounds repeated queue recovery dispatches while allowing lease takeover. |
| `P5_SHORT_ANSWER_MAX_CHARS_V1` | 3000 | Maximum saved short-answer length. |
| `P5_REPORT_MAX_CHARS_V1` | 2000 | Maximum student report length; duplicate submission returns the original report. |
| `P5_REVIEW_PAGE_SIZE_V1` | 50 | Maximum default reviewer queue page size. |
| `P5_GRADING_REVIEWER_EMAILS` | empty | Separate reviewer allowlist; evaluator access grants no review access. Empty closes reviewer endpoints. |

Existing mastery formulas, evidence weights, decay, recommendation weights, and
selection ordering are unchanged. Diagnostics and existing cached/active MCQ sets
remain usable. Linked-course grounding remains P8.

## Preparation, grading, and feedback

- New P2/P4 preparation uses typed mixed-question schemas before persistence. A short
  answer stores its prompt, expected reasoning, concept attribution, three default
  rubric criteria, criterion reasoning, and source provenance. The validator checks
  answerability and support/relevance of every criterion against owned current-course
  chunks. P4 isolation and taught-concept eligibility still apply.
- Questions remain immutable versions. Fixed sessions retain the saved question IDs,
  versions, order, and types; resume does not replace a question set.
- Confirming an answer persists it before Celery grading dispatch. A grading lease,
  saved provider-call count/limit, dispatch time, and attempt status support duplicate
  delivery recovery. The call limit is snapshotted when the answer is saved; the binary
  passing threshold is snapshotted on its immutable question version. Expired leases can
  be taken over; attempts and evidence remain unique per fixed assessment question.
- Provider calls use the configured Gemini gateway and existing atomic daily allowance.
  Malformed/provider-failed grading stays unresolved; after the bounded calls the
  answer remains saved and is marked retry-required or exhausted with a safe failure
  reason. Concurrent retries lock and refresh the saved answer before changing state.
  Session grading states distinguish `AWAITING_GRADING`, `RETRY_REQUIRED`, `EXHAUSTED`,
  and `COMPLETE`. Assessment polling continues only while grading is queued or running.
  No failed or pending judgment contributes evidence. Retry uses the exact saved answer
  and question version.
- Feedback appears only after the full set is submitted. Short-answer feedback lists
  each criterion and its supported reasoning/source, shows the 0–3 default rubric
  count separately from the binary correct/incorrect evidence label, shows the saved
  threshold for that immutable question version, and explains that automated grading can
  be wrong. P3 comparisons use the existing mastery service and fixed session attribution.
  Reading completion still changes coverage only.

## Reports, review, corrections, and retention

- Only the authenticated owner can report a saved, graded short-answer judgment after
  set feedback is available. One idempotent report is kept per saved judgment; a
  duplicate returns the existing report and does not replace its text or imply another
  attempt. The acknowledgment does not promise immediate review.
- Review access uses only the separate email allowlist. Reviewers can inspect the
  reported answer, fixed question/rubric version, original judgment, and cited course
  context. Unrelated learners cannot inspect reports.
- Report states are `OPEN → IN_REVIEW → RETAINED` or `OPEN/IN_REVIEW → CORRECTED`.
  Reviewer reasons and state transitions are append-only. Corrections append versioned
  criterion judgments; the latest approved correction determines the effective binary
  evidence. A stale correction version is rejected.
- Raw `QuestionAttempt` and `MasteryEvent` evidence and the original automated judgment
  remain unchanged. Effective correction is applied through a versioned record in the
  existing mastery query path, including mastery reports, P3 comparisons, recommendation
  inputs, and attributed outcome views. Existing recommendation decisions are not
  rewritten; a correction does not replace an activity already started or add an attempt.
- Report, judgment, correction, and review history are retained until account or course
  deletion, then deleted with that learning data.
- Deleting a reviewer account clears that reviewer's identity from audit/correction rows
  while preserving the learner's effective correction and report history until the
  learner's account or course is deleted.

## API and persistence

Routes live under the existing learning API:

- `POST /courses/{course_id}/assessment-sessions/{session_id}/questions/{question_id}/grading-issue`
- `GET /grading-reviews?status=OPEN&offset=0` (reviewer queue returns configured
  page size, `has_more`, and `next_offset`), `GET /grading-reviews/{report_id}`
- `POST /grading-reviews/{report_id}/start`, `/retain`, and `/correct`
- Existing fixed-session answer, retry, submit, resume, learning-state, and results routes
  continue to be authoritative.

Migration `f5a1c9d2e7b4_grounded_short_answer_review` adds rubric reasoning/provenance and
saved threshold fields, durable grading attempt/lease/call-limit fields, original
judgments, reports, review events, and versioned corrections. Reviewer actor references
can be anonymized on reviewer-account deletion without removing learner corrections.
OpenAPI remains authoritative; generated frontend types are in
`frontend/lib/generated/api.ts`.

## Verification and limits

Local deterministic fixtures cover valid and rejected rubrics, fixed mixed sessions,
answer locking, interruption/lease recovery, duplicate evidence protection, pending
feedback, owner/reviewer access, report idempotency, correction propagation, historical
decision preservation, and deletion. Executed locally on 2026-10-08:

The end-to-end correction regression also records an attributed transfer outcome before
review and verifies that its history view reflects the corrected effective judgment.

- `/private/tmp/neurolearn-p1-venv/bin/pytest -o addopts= --disable-warnings -q tests`:
  689 passed, 19 skipped, 1 warning.
- `env P1_TEST_DATABASE_URL=postgresql://postgres:password@127.0.0.1:5433/neurolearn_p5_pgtests_20261008 /private/tmp/neurolearn-p1-venv/bin/pytest -o addopts= --disable-warnings -q tests/integration/test_learning_postgresql.py`:
  7 passed, 1 warning. This used the existing disposable local PostgreSQL service,
  including the concurrent retry/worker completion regression.
- `/private/tmp/neurolearn-p1-venv/bin/ruff check app tests --output-format concise` and
  `/private/tmp/neurolearn-p1-venv/bin/python -m compileall -q app tests`: passed.
- `npm run lint`, `npm run typecheck`, and `npm test`: passed; 7 frontend tests.
- `npm run build` with build-only placeholder credentials: passed.
- `python scripts/export_openapi.py --check` and
  `./node_modules/.bin/openapi-typescript ../backend/openapi.json -o lib/generated/api.ts --check`:
  passed; `git diff --check`: passed.
- The full Alembic chain upgraded the fresh disposable database
  `neurolearn_p5_final_20261008` through head `f5a1c9d2e7b4`.

Automated checks used deterministic provider fixtures and did not spend live AI quota.
These are local results; no production migration, deployment, or hosted worker/provider
check was performed.

Hosted workers, live Gemini behavior, deployed reviewer configuration, and provider
allowance behavior are not demonstrated here. Defaults have not been calibrated against
learner outcomes. P6 can rely on the fixed session/result/report API, source links,
pending/retry states, correction-aware progress, and the direct dashboard exit; full
workspace and side-panel integration stays in P6.
