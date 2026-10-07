# Implementation gaps

Baseline: code inspected on `develop`, 2026-10-07. Historical product descriptions are
superseded by this plan where they conflict. “Present” means code exists, not that runtime
behavior is proven.

Later checks inspected the Celery dispatch seam and researched official Celery/Upstash
support. They did not verify a hosted deployment; Railway Redis is now the approved target.

Paths below are relative to repository root.

| Area | Present / code evidence | Required change |
| --- | --- | --- |
| Identity/dashboard | `frontend/auth.ts`; `frontend/app/(pages)/dashboard/page.tsx` | Retain Google flow; unify actions with saved learning state |
| Setup/processing | `backend/app/modules/documents/service.py`; `jobs/service.py`; frontend `courses/[courseId]/workspace/page.tsx` | Reuse pipeline; pre-publication replacement with dependent-artifact invalidation/revalidation |
| Review/publish | `curriculum/router.py` supports lesson renames and publication | Add module rename; optional grounded prerequisite warnings |
| Subjects/links | Inspected `courses/models.py` has no subject/link fields | New ownership-scoped relationships and matching/evidence policy |
| Curriculum | `curriculum/service.py` extracts concepts, builds/validates versions, creates blueprints | Reuse; blueprints are not generated lesson assessments |
| Diagnostic | P1 persists/resumes fixed question ID/version/order sets and distinguishes saved answers from grading; small graphs may yield fewer questions than the PDF's stated minimum | Later define skip/retry policy; do not restore historical minimum counts as guarantees |
| Lesson assessment | P1 activity sessions reuse existing questions for the saved course version and target concepts | **P2 implemented locally:** grounded MCQs are generated for the prepared activity, versioned, and attached to its fixed P1 session; see [P2 contract](p2-grounded-preparation-contract.md) |
| Grading/evidence | P1 persists answers independently, gates feedback until set submission, retries failed grading, and deduplicates existing attempt/evidence writes | Later add durable grading workers and authorized corrections preserving original judgments |
| Mastery | `mastery/engine.py` computes weighted prior/uncertainty/decay | **P3 exposes approved uncalibrated bands and separate evidence strength;** no second mastery store or calibration claim |
| Selection | P1 persists the selected recommendation trace with one unfinished activity per owner/course and resumes it before selecting again | **P3/P4 Continue uses this path and preserves the decision/activity trace;** P4 adjusts eligibility/targets only and retains existing ranking weights and ordering |
| Activity navigation | P1 learn/study/assessment consumers save position/format, resume fixed sessions, and provide a dashboard exit | **P3 adds results/progress; P4 adds distinct remediation, targeted-practice, and challenge states.** Full course overview and side-panel integration remain P6 |
| Teaching/tutor | `tutor/service.py`; study/tutor/source pages | Consistent teaching structure; contextual side panels; assessment restriction |
| Presentation | Study page sends learner-button success before assessment | Replace learning-effectiveness signal with attributed graded outcomes |
| Progress/resume | P1 persists reading position, fixed assessment questions/answers, activity/session states, and lesson coverage; P3 derives concept comparisons at the saved submission time | P9 still needs agreed course-completion/retention criteria; no second progress store |
| Account controls | `identity/router.py`, `privacy/service.py`; existing profile UI | Wire product settings; review deletion/retention for every new entity |
| Legacy experience | `chat/router.py`, `content/router.py`, profile use learning-style data | Decide navigation placement; don't present it as the new course adaptation |

Additional deployment gaps:

| Area | Inspected evidence | Required change |
| --- | --- | --- |
| Workers | `backend/app/core/celery_app.py`; jobs and preparation tasks use PostgreSQL stages/leases and Celery priority | **P2 implemented locally:** prioritized first-activity preparation, one content-only lookahead, deduplication, retry/reuse; grading-worker recovery remains later |
| Grounding | `tutor/validation.py`; P2 typed lesson/MCQ schemas and per-claim validation | **P2 checks every displayed lesson statement and generated MCQ against current-course owned chunks.** Explicit linked-course retrieval remains P8. |
| Hosting/storage | P0 repository config now includes production API/worker commands and private Supabase S3 uploads; no hosted behavior verified | Configure provider projects, run the controlled migration, then verify networking, credentials, storage, worker recovery, and persistence |
| AI accounting | `abuse/service.py` counts controlled requests; some limits are process-local | Include worker/validation/retry provider work; enforce deployed cross-process budgets/concurrency |

Module paths without full prefixes above refer to `backend/app/modules/`.

## PDF mismatches relevant to this plan

- PDF claims a complete repeated assessment loop; lesson assessments remain missing.
- PDF describes whole-response abstention on any citation failure; tutor code retries,
  strips failed claims, and may retain surviving content. Agreed target checks all factual
  claims and abstains when the supported remainder is inadequate.
- PDF remediation bonus differs from code; neither value is empirically validated.
- PDF describes forced exploration of every format; code periodically chooses a runner-up.
- PDF's reported test/live/injection results have not been reproduced on this checkout.
- PDF limits study files to two; inspected upload service allows five. Upload policy needs
  an explicit decision, not silent adoption of either number.

## Contract/dependency warning

Activity lifecycle, assessment sets, subjects/links, rubric feedback, and grading reports
may need schema/API changes. Design contracts and migrations before consumers; regenerate
OpenAPI-derived frontend types. Existing safeguards must be reviewed for new paths,
especially linked retrieval, reviewer access, replacement, pending grading, and worker
limits. Targets are selected in [README.md](README.md); hosted compatibility/configuration
remain unverified. This is an inspection snapshot, not a new exhaustive audit.

## P0 foundation status — 2026-10-07

**Implemented in repository:** `backend/Dockerfile` defaults to its `production` stage,
which starts production Uvicorn without
reload, using Railway `PORT`; Compose keeps its local development override. Railway API
and worker commands are `./start-api.sh` and `./start-worker.sh`. Configure Railway API
readiness as `/health/db`, and run `alembic upgrade head` once from the API service's
pre-deploy command. Vercel uses `frontend/` and the existing `npm run build`; no custom
Vercel routing is needed. Environment names and placement are in the two `.env.example`
files and [README.md](README.md). S3 direct uploads use only `Content-Type` in signed PUTs;
the API checks object size and streams it back for SHA-256 verification at finalization,
avoiding unsupported S3 checksum headers. The worker has no inbound port or HTTP probe;
its service state and PostgreSQL lease heartbeats are the operational signals.

**Code inspection only:** PostgreSQL is authoritative; Redis is Celery coordination.
The worker uses late acknowledgment, bounded task time limits, and the job service fences
writes with PostgreSQL lease tokens and heartbeats. A durable job that cannot dispatch is
marked paused; recovery requires the learner's explicit retry. Do not describe this as
automatic recovery. The migration chain has one head. An existing guard blocks the
historical `2f4c2d25f29c` upgrade when
`articles` or `paragraphs` contain rows at the protected baseline. This does not replace
inspection of the actual Supabase schema before deployment.

**Executed locally:** `npm test` (6/6), `npm run lint`, `npm run typecheck`, and
`npm run build` all passed; build used placeholder backend/auth values. Python
`compileall`, `sh -n` for both startup scripts, and `git diff --check` passed. A static
Alembic revision-chain inspection found 29 revisions, one head, and no missing parents.
Focused backend tests could not start because the host only has Python 3.14 (project
baseline is 3.11); installing pinned requirements failed because Python 3.14 requires
`grpcio-status >=1.75.1` while the repo constraint pins `1.71.2`. Ruff and Alembic
commands were unavailable in this environment. No provider account, linked Railway
project, running Docker daemon, or deployed URL was available. No migration was run and
no database, user data, or provider configuration was changed.

**Unverified / remaining:** create or identify Vercel, Railway API/worker/Redis, and
Supabase PostgreSQL/private bucket resources; set environment variables in each service;
verify browser PUTs from the deployed web origin (Supabase's S3 API does not implement
`PutBucketCors`); register the exact Google callback; run migrations on an inspected
database; exercise OAuth, pgvector, private storage, queue processing, worker interruption,
and persistence across restarts. No production URLs are known or claimed.

## P1 implementation update — 2026-10-07

**Implemented locally:** durable owner/course/version-scoped activities; recommendation
decision trace persistence; saved reading position and format; fixed diagnostic/activity
assessment question ID/version/order snapshots; immutable confirmed answers; delayed
feedback; grading retry state; one attempt/evidence write per fixed question; lesson
coverage separate from concept-understanding responses; explicit account-deletion
cleanup; additive migration `a61c9e7d4b20`; generated OpenAPI/TypeScript contracts; and
minimal learn/study/assessment consumer updates. Details and precise routes are in
[p1-lifecycle-contract.md](p1-lifecycle-contract.md).

**Still open for later milestones:** durable worker grading/recovery, short-answer AI
grading, calibrated mastery/completion criteria, and the full learning-screen design.
P2 now provides activity-scoped lesson questions and asynchronous preparation. These gaps
do not change the verified P1 contract.

## P2 implementation update — 2026-10-08

P2 is implemented locally on `feat/p2-grounded-preparation`. Migration
`b2e7c19a4d63_async_grounded_preparation` adds durable preparation/content,
citations, per-preparation question membership, question source provenance, and
normalized-prompt uniqueness. FastAPI OpenAPI is regenerated in `backend/openapi.json`;
generated frontend types are in `frontend/lib/generated/api.ts`. Exact behavior,
approved defaults, API paths, verification commands/results, and remaining limits are
in [P2 contract](p2-grounded-preparation-contract.md).

The first course remains learner-owned and current-course grounded. P2 does not add
linked-course retrieval, short-answer grading/review, new recommendation or mastery
rules, or production migration/deployment. Hosted worker capacity and live Gemini
behavior remain unverified.

## P3 implementation update — 2026-10-08

P3 connects submitted P1/P2 sessions to before/after concept labels, separate evidence
strength, lesson coverage, and Continue. Results are reconstructed through the existing
mastery engine from immutable evidence at the saved assessment submission time. Pending
grades remain unresolved; retries add no second evidence event. `LearningActivity` keeps
the course version, decision, targets, reason, and format. The existing active-activity
guard and course lock protect concurrent Continue requests. P3 originally requested P2
preparation only for supported lessons. P4 now supports three additional selected
activity types through the same lifecycle.

No P3 migration or new learning record is required. OpenAPI and generated frontend types
include `evidence_strength`, submitted-session grade counts/concept progress, and activity
experience availability. Approved display defaults and verification evidence are in
[P3 contract](p3-results-progress-contract.md). Hosted behavior is not claimed.

## P4 implementation update — 2026-10-08

P4 adds evidence-gated concept remediation, question-first targeted practice and
challenge, explicit single-concept fallback wording, and one-concept-per-question
challenge attribution. Preparation reuses P2 jobs and validators with purpose/target
scoped keys; the additive migration is `d3f4a8c1e620`. P1 fixed assessments and P3
results/Continue remain authoritative. Numeric defaults, migration, API, verification,
and limits are recorded in [P4 contract](p4-adaptive-activities-contract.md). Hosted
behavior is not verified.
