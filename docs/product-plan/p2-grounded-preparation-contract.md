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
| Validation request bound | `P2_VALIDATION_BATCH_SIZE_V1` | Unvalidated operational bound of 12 independent checks per request, grouped only when their exact cited source text matches |

## Preparation and publication

Ingestion and curriculum services remain the upload, extraction, source-indexing,
outline-validation, and publication path. `POST /courses/{course_id}/publish-structure`
queues the first eligible lesson and MCQ set after the validated version is activated.
The preparation is durable and initially has no activity ID; when P1 selects the matching
first lesson, the existing row and its artifacts are attached to that activity. Navigation
still creates or resumes preparation as a recovery path. The learner can open validated
saved lesson text as soon as the content stage finishes. No other lessons or formats block
first-activity readiness.

Lesson-content prompt version `p2-lesson-content-v3` requests a compact,
source-checkable draft: one short explanation statement per concept plus a
typed instructional objective, a source-based example, and a recap. Objectives
carry a constrained action, one target concept, and mapped source provenance;
they contain no free-form factual claim. Explanation, example, and recap remain
factual statements checked against the cited passages.

PostgreSQL stores preparation status, stage, progress, errors, retry/candidate/provider
call counts, lease/dispatch timestamps, and artifact keys. Celery delivery is
deduplicated by durable publication-first, activity, and version/format keys, with
student work at priority 0 and the optional lookahead at priority 9. Workers lock and
refresh the preparation row before artifact/question writes and completion/failure state
changes; a stale lease token or expired lease cannot commit. Each AI request reserves its
daily allowance through an atomic owner/date increment, unless the owner's verified
email is explicitly listed in `AI_BUDGET_EXEMPT_EMAILS` (empty by default). This skips
the app threshold but still records usage; provider limits and other controls remain
active. Lease expiry
permits a pending task to be redispatched. A retry resumes at content or questions based on saved artifacts
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

Lesson output is parsed through strict Pydantic schemas. Objective items use a
constrained action, one target concept, and citations mapped to that concept. The
service renders the instruction from those typed fields; objective text is not sent
to factual entailment. Each explanation, example, and recap statement must cover
listed concepts and cite current-course chunks owned by the learner. Every citation
must map to a concept in the statement, and all cited passages are checked together
to establish the complete factual claim. Any unchecked, unsupported, malformed, or
inadequately covered factual section rejects the candidate. Abstention text is never
stored as content. Saved artifacts preserve the source and curriculum fingerprints,
course version, presentation format, chunk citations, generator/validator model IDs,
prompt/schema/validation-policy versions, validation state, and timestamp.

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

### Bounded validation requests — 2026-10-08

Preparation groups support checks into strict JSON requests. Each batch contains only
the exact cited passage (or combined passage text) shared by its checks; unrelated
passages are never included to support another check. Ownership, citation mapping,
all-claim coverage, answer uniqueness, rubric checks, and isolated attribution remain
required. Tutor sampling is unchanged by this preparation optimization.

### Directed diagrams — 2026-10-09

Diagram drafts add `diagram_edges` to the existing four-section content contract.
Each edge has strict zero-based `from_index`/`to_index` references to explanation nodes,
a complete relationship statement, endpoint concept IDs, and citations. The existing
12-node explanation bound is retained; at most 16 edges are accepted. At least two
nodes and one supported connection are required. The validator checks endpoint text,
relationship, and direction against the edge's owned mapped sources, without combining
unrelated evidence into a batch. Unsupported nodes or connections reject the candidate;
node indexes are never silently remapped after stripping.

Diagram provenance uses `p6-diagram-content-v1`, `p6-diagram-schema-v1`, and
`p6-diagram-grounding-v1`, including those versions in the artifact key. Old diagram
text remains stored but cannot satisfy the connection contract. An inactive old
preparation can resume its content stage while retaining fixed question memberships
and assessment identity. Other cached formats retain their existing keys. No database
migration is needed: checked connections live in the artifact's existing section JSON
and reuse its citation relations.

Source-view and quiz-first interfaces use existing validated content and owned source
reads. Quiz-first warm-ups use instructional objectives rather than exposing the
prepared assessment questions or issuing new grading calls.

Every requested check ID must appear exactly once with a strict boolean result.
Missing, duplicate, unknown, malformed, or nonboolean results raise validation
unavailability; they never become negative distractor judgments. Successful exact
claim/source pairs are reused only within the checker for that preparation stage.
Failed batch responses are not cached as judgments. The daily allowance and preparation
counter reserve one slot per actual gateway request, including each batch and retry.
The 200-call ordinary account threshold is unchanged.

The Gemini gateway logs model, elapsed time, application-level provider attempt count,
and rate-limit backoff intervals without prompts, responses, or exception payloads.
These records distinguish the gateway's own retries from its counted outer call;
they do not claim complete SDK transport or token accounting.

Live comparison replayed the previously captured real Distributed Systems lesson and
mixed question draft through the new production validators without replacing learning
records. The final source-isolated implementation used **9 validation calls in 8.965
seconds**, versus **47 in 197.292 seconds** previously. All required support checks
passed. This is one validation-stage comparison, not a full fresh-generation latency
guarantee. The draft generation calls and browser learning cycle were not repeated.

Verification: **140 focused backend checks** plus **2 PostgreSQL concurrency/atomic
allowance checks** passed; Ruff and `git diff --check` passed. PostgreSQL checks used
a fresh disposable database because the prior test database contained older public
tables. No product database schema, saved question IDs, or grading rules changed.

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

## Live lesson-preparation failure trace and correction — 2026-10-08

### Inspection

- Reused the healthy local Compose stack. The course's published version was READY,
  and its processing job was READY. The selected activity and preparation referenced
  that version and four targets: Availability and Failure Scope; Failure Domains and
  Redundancy; Network Partitions and Consistency Trade-offs; Idempotency Keys and
  Client Retries.
- The course had three extracted study files and 16 chunks. Each selected concept was
  mapped to the same six chunks from the section used to derive those concepts; every
  mapped chunk was owned by the course owner and on extraction version 1. These are
  chunks from study document `93e678b5-9a65-4b48-a8d8-ed628620e753`. The six chunks
  fit within the 12-chunk preparation bound. The source selector returned all six
  mapped chunks, so this trace found no ownership, version, ordering, or context
  truncation defect. Source text was not copied to logs or this document.
- Course `415aafb3-e7d2-4696-869a-f3840a28d7c4` preparation
  `acf0540d-2966-4f0b-b5fc-e4a23d1b1416` was the concise retry for activity
  `e6155757-474d-41bf-8d6a-5dca51f9283b`, lesson
  `b2367b80-c515-424f-96eb-faef4372b2a3`, version
  `180b3992-9503-4b46-8cca-e23e2c533763`, and processing job
  `c3086426-00df-494b-b58d-3d68d7fa903b`. It ended in CONTENT with 3 candidates,
  110 provider/checker calls, and `INSUFFICIENT_SOURCE_SUPPORT`; no content artifact
  or prepared questions existed. Preceding detailed preparation
  `36813d17-e9da-4025-adce-7d3c2fef396e` also failed CONTENT after 3 candidates and
  51 calls. The configured gateway was
  `GeminiGenerationGateway` / `gemini-3.5-flash-lite`.
- The database stores only the last candidate's error category, not each candidate's
  result or draft. Therefore the historical row cannot prove that all three candidates
  failed for the same reason. Today’s owner allowance was already 200/200; no further
  provider attempt was made. A separate detailed preparation had assessment enabled,
  but it failed at CONTENT; no question stage or readiness was reached.

### Confirmed defects and correction

- The prompt required a learning objective, while the schema represented it as free
  text and the validator checked it as a factual claim. A deterministic regression
  reproduced the same `INSUFFICIENT_SOURCE_SUPPORT` rejection when factual sections
  were supported but the instructional objective was not a source assertion.
- A statement with multiple citations was expanded into one claim per cited chunk,
  and each chunk was independently required to entail the entire statement. This
  rejected valid claims whose cited passages support them together and repeated the
  same semantic provider check. The validator now checks the union of cited passages
  once while checking ownership for every chunk. Every citation must still map to the
  statement's concepts, and every factual statement must pass semantic validation.
- Objectives now use a typed action and concept ID, retain mapped citations, and are
  rendered as instructional text. Prompt, schema, and validation versions were bumped;
  previously validated P2/P4 artifacts retain compatible cache lookup paths. Safe
  candidate logs record only preparation ID, attempt number, and stage/reason code.

### Deterministic execution

- Red regression before the objective fix:
  `/private/tmp/neurolearn-p1-venv/bin/pytest -o addopts= --disable-warnings -q tests/api/test_preparation.py::test_learning_objective_is_not_misclassified_as_a_factual_source_claim`
  — **failed** with `CandidateRejected: INSUFFICIENT_SOURCE_SUPPORT`.
- Focused preparation and validator suite after the correction — **45 passed**.
  Full backend suite — **699 passed, 19 skipped, 2 warnings**. Backend Ruff — passed.
- `npm run contracts:check` — passed with no API contract change. Frontend tests —
  **40 passed**; lint, typecheck, and production build — passed (build used placeholder
  backend/auth values). The build route list includes the diagnostic route. P6's
  existing 393 px diagnostic inspection and unfinished-activity guard remain recorded
  in [P6 screen mapping](p6-screen-mapping.md).
- The existing unsupported-claim abstention regression passed within the preparation
  suite. Saved-content reuse remains covered deterministically, including compatibility
  lookup for already validated P2/P4 artifacts.

### Live execution and remaining blocker

The pre-correction live attempts remain failures with no saved lesson. A post-correction
live generation, second saved-content read, unsupported-material live abstention, and
assessment preparation were not run because the local daily provider allowance was
exhausted at 200/200. They are **blocked**, not successful. The current course's source
sufficiency is not established by chunk presence or mappings alone; no validated live
lesson exists to demonstrate it. There is no evidence that the course lacks support,
and the corrected pipeline has two deterministic validation regressions covered. Before
P7, repeat one bounded live run after allowance reset: demonstrate a supported saved
lesson and a read with no generation, confirm unsupported material still abstains, then
trace question readiness separately. Do not treat assessment readiness as established
by lesson validation.

### Developer allowance follow-up — 2026-10-08

- `AI_BUDGET_EXEMPT_EMAILS` is a separate, empty-by-default allowlist; it does not
  reuse evaluator access. The private local `.env` lists the owners of the two courses
  used in this developer check. Backend and worker were recreated and confirmed to
  load the configured Gemini key/model and exemption without printing credentials.
- The currently open course differed from the original trace: course
  `218d5473-d4c4-41b9-98d5-be124a27a13f`, activity
  `92ef5d4f-dd2b-4300-bb5a-f1d7702fe7e8`, lesson
  `dace8541-b322-4609-b279-9eddaf1c73c9`. One retry transitioned concise preparation
  `a0a87efc-6d6b-418b-8765-0d7817bb76ef` to READY/COMPLETE and linked artifact
  `1bea6ff4-6d21-4112-8849-767876c9e286`, recorded PASSED with three citation rows.
  The browser rendered it, and a reload served the same saved artifact without a new
  provider call. The original allowance message disappeared; the UI correctly reports
  that questions or other formats still need preparation.
- The first exemption implementation returned before recording usage, so that retry's
  zero provider-call count cannot prove the new Gemini key was exercised. The exemption
  now bypasses the threshold while still incrementing the daily usage counter. Its
  regression test starts at 200 and verifies an allowlisted call is recorded as 201.
  No uncached provider generation was run after this accounting correction, so key
  validity against Gemini remains unverified.
- Assessment readiness remains separate and unavailable: the concise artifact's
  preparation has `include_assessment=false`; the detailed and worked-example
  assessment-enabled preparations remain failed at CONTENT with zero prepared
  questions. A live assessment preparation was not retried. The original course's
  source sufficiency also remains unestablished.

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
