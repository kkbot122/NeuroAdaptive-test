# NeuroLearn product plan

Status: agreed product direction. P0 repository configuration and P1–P4 learning
contracts are implemented locally; hosted deployment and runtime pass conditions have
not been demonstrated. Date: 2026-10-08.

## Read in this order

| File | Use it to answer |
| --- | --- |
| [user-flows.md](user-flows.md) | What does the student do from arrival to completion? |
| [screens.md](screens.md) | What appears on each screen, and what actions are available? |
| [learning-rules.md](learning-rules.md) | How do teaching, assessment, mastery, and selection behave? |
| [p1-lifecycle-contract.md](p1-lifecycle-contract.md) | What durable P1 states, API payloads, and P2 seams are implemented? |
| [p2-grounded-preparation-contract.md](p2-grounded-preparation-contract.md) | How are first-activity content and lesson MCQs prepared, validated, and reused? |
| [p3-results-progress-contract.md](p3-results-progress-contract.md) | How do submitted results, concept evidence, and the next activity connect? |
| [p4-adaptive-activities-contract.md](p4-adaptive-activities-contract.md) | How do remediation, targeted practice, and challenge use saved recommendations? |
| [implementation-gap.md](implementation-gap.md) | What can we reuse, and what is missing? |
| [build-plan.md](build-plan.md) | What should we implement first, and how do we demonstrate it? |

## Product commitment

Turn the student's own material into a reviewable course, grounded teaching,
assessment evidence, and a guided next activity. Finish repeated learning cycles
before expanding feature breadth. Show source support and uncertainty; do not
claim improved learning without a suitable study.

Agreed boundaries:

- Continue studying selects the activity; the outline is available to inspect.
- Diagnostic is optional. Reading completion is separate from understanding.
- MCQ and short answer are the planned assessment types.
- Published sources stay fixed. Subjects and links to earlier courses are optional.
- Missing prerequisite coverage warns, but does not block publication.
- Tutor and source viewer sit beside the activity; tutor help is unavailable during assessments.
- Completion separates lesson coverage from demonstrated understanding.
- Optional practice after completion is selected by the app.

## Deployment and preparation decisions

Selected target, not a verified deployment:

| Component | Target / role |
| --- | --- |
| Web | Next.js on Vercel |
| API | FastAPI on Railway |
| Workers | Celery on Railway; asynchronous processing, preparation, and grading |
| Queue coordination | Redis on Railway; Upstash is excluded |
| Product data | Supabase PostgreSQL with pgvector; authoritative progress/jobs/evidence |
| Original sources | Private Supabase Storage; owner-authorized access for API/workers |
| AI | Configurable Gemini generation and embeddings; no automatic provider fallback |

### P0 repository deployment configuration

Use Vercel with root directory `frontend` and its existing `npm run build`, plus two
Railway services built from `backend/`: API (`./start-api.sh`) and worker
(`./start-worker.sh`). The Dockerfile's default target is `production`; set the
worker's start command override to `./start-worker.sh`. Set Railway API readiness to
`/health/db`. Configure `alembic upgrade head` as the API service's pre-deploy command;
Railway runs it before API deployments, while the worker has no migration command. Set
Supabase direct PostgreSQL `DATABASE_URL` on API and worker (use the session pooler only
if the runtime network requires IPv4), Railway Redis URL as both Celery URLs, and Supabase
`STORAGE_S3_ENDPOINT`, `STORAGE_S3_REGION`, `STORAGE_S3_ACCESS_KEY`,
`STORAGE_S3_SECRET_KEY`, and `STORAGE_BUCKET` on API and worker. Set `SECRET_KEY`,
`INTERNAL_API_KEY`, `GEMINI_API_KEY`, and Celery URLs on both Railway services; set
`FRONTEND_URL` on the API. Keep storage credentials server-side. Keep Google OAuth
credentials in Vercel's server-side environment.
Set Vercel `INTERNAL_API_URL` to the Railway API public HTTPS origin and the same
`INTERNAL_API_KEY`; set `NEXTAUTH_URL`, `NEXTAUTH_SECRET`, Google client credentials,
and `AUTH_TRUST_HOST` there. Register the exact Google callback URI
`https://<production-host>/api/auth/callback/google`. These instructions do not show
that provider accounts are configured or services deployed.

The API listens on Railway-provided `PORT` and has HTTP database readiness at
`/health/db`. The worker needs no inbound port or public domain; its progress is stored
as PostgreSQL stages and lease heartbeats. Watch worker service state and job leases to
identify interruption; retry is user-triggered. Vercel uses the public Railway API origin.

Prepare the first lesson and assessment as soon as required source/outline artifacts
are valid. Studying starts after outline publication and first-activity readiness;
it does not wait for all lessons or variants. Save validated content, reuse variants,
and progressively prepare likely next activities. Prioritize waiting students,
then first-course readiness, then speculative preparation. Background work must
leave capacity for interactive requests.

The speed goal is responsive deployed interactions, not an unmeasured latency
claim. Measure preparation, saved-content loading, tutor validation, and grading.
Demo both a real prepared course and a fresh upload through the production path.

## Safeguards agreed

- Teaching/answers use the current course and explicitly linked earlier courses only.
- Check source ownership/existence and semantic support for every factual claim.
  Remove unsupported claims; abstain if the remainder cannot answer adequately.
- Private storage requires server-side ownership checks; files are not public.
- Bounded preparation, retries, and AI allowances; saved content/progress remain
  accessible when generation pauses. Pending grading is not incorrect evidence.
- Required learning records are separate from optional interaction tracking.
- Authorized grading reviewers can correct judgments, preserving originals and
  recomputing affected evidence without counting another attempt.
- Before publication, replacement preserves valid files and rebuilds dependent
  artifacts. Published sources stay fixed.

## Decisions still open

| Decision | Must be settled before |
| --- | --- |
| Completion criteria and decay policy | P9 completion/retention policy |
| Short-answer rubric and review policy; calibration of mastery/selection rules | P5 policy acceptance / later calibration |
| Reliable cross-course concept matching and evidence reuse | Linked-course integration |
| Missing-prerequisite detection and supporting evidence for warnings | Prerequisite-warning release |
| Reviewer access, review operations, retention, and correction propagation | Report-grading-issue release |
| What ends an activity whose assessment stays unavailable | Full recovery acceptance |
| Hosted worker capacity, cross-process concurrency, and operational AI limits | Hosted worker acceptance |
| Provider eligibility, region/network configuration, and storage credentials | Hosted verification |
| Retention/deletion details for new entities and complete short-answer claim coverage | Safeguard acceptance |

P2 defaults are approved for implementation, but unvalidated. P3 also approves the
existing uncalibrated mastery-label cutoffs and the versioned evidence-strength display
boundary; details are in the P3 contract. These do not change mastery formulas, decay,
or recommendation scoring. P4 approves five MCQs for each remediation, practice, and
challenge set, with the P2 cap of eight; these named, versioned, configurable defaults
are unvalidated and recorded in the P4 contract. Other numeric defaults are unvalidated;
future tunable numbers need named, versioned, configurable policies.

## Scope and authority

This folder records approved product/architecture decisions and implementation evidence;
it is not a deployment claim. Operational rules for migrations, contracts, ownership,
and honest reporting still apply. P1–P4 status/checks are recorded in their contracts
and [implementation-gap.md](implementation-gap.md).

Explicit scope revisions: linked-course grounding/evidence, visible factual selection
reasons, replacement before publication, support checks for all factual claims, and
authorized grading review. Railway Redis replaces Upstash. These revisions supersede
earlier product descriptions where they conflict; revise contracts before implementing.
Evidence decay/completion interaction remains open. Prerequisite remediation is
app-selected work, not a publication block for missing source coverage.
