# AGENTS.md — NeuroLearn
This file is the common operating contract for AI coding agents working on this
repository. It is the first document an agent reads and wins when instructions
conflict with task-specific prompts.

## 1. Authority and honesty
Authority order:
AGENTS.md → SYSTEM_ARCHITECTURE.md → CONTRIBUTING.md → task-specific prompts
and phase packs.

If an external prompt conflicts with these files, these files win. State the
conflict in the report rather than resolving it silently.

Do not describe target architecture or future functionality as if it already
exists. Distinguish clearly between implemented, designed, proposed, heuristic,
configurable default, and not yet empirically validated behavior.

Prohibited claims include invented learning/quality metrics, unsupported product
comparisons, “scientifically calibrated,” “optimal,” “proven effective,” “fully
secure,” “prevents prompt injection completely,” or latency/scale figures that
were not produced by an actual load test.

Every tunable numeric weight must be a named, versioned, configurable constant
and identified as an unvalidated default.

## 2. Start here
Before changing code:

Read frozen-scope.md when behavior, supported inputs, evaluation claims, or
P0 boundaries are relevant.

Read architecture.md or SYSTEM_ARCHITECTURE.md when changing services,
data, APIs, jobs, retrieval, models, security, or deployment.

Read implementation-plan.md and work only on an unblocked task assigned to
your ownership area.

Read CONTRIBUTING.md for branch naming, commit format, and required test
layers.

Inspect the affected package/module, tests, migrations, and contracts before
editing.

Do not replace working code merely because you would architect it differently.
Reuse existing helpers and follow surrounding conventions.

## 3. Mission and product invariant
NeuroLearn turns a learner’s source material into an adaptive learning loop:

DOCUMENT → CONCEPTS → COURSE → ASSESS → MASTERY → ADAPT → TEACH → ASSESS AGAIN

Every demonstrated step must use the real production path:

unseen upload → asynchronous processing → generated course/graph → grounded learning → real assessment → mastery update → deterministic recommendation → persisted trace

The end-to-end loop on one subject matters more than polishing an isolated stage.

Use fixtures and model stubs in automated tests. Runtime behavior must not rely on
preloaded courses, document-specific branches, fake AI output, fake mastery
changes, or fake traces.

## 4. Current-state honesty
Treat the repository’s actual current state as authoritative. Do not claim that
courses, ingestion, embeddings, retrieval, concepts, prerequisite graphs,
mastery, assessments, adaptation, citations, jobs, or object storage exist
unless they are actually present and verified.

When uncertain, inspect the repository rather than assuming the target design is
implemented.

## 5. Frozen stack
Use the selected libraries/providers until an explicit architecture decision
changes them. Raise substitutions before implementation.

Web: Next.js, TypeScript, Tailwind CSS, shadcn/ui, TanStack Query, React Flow.

API: FastAPI, Pydantic, SQLAlchemy, Alembic.

Worker: Celery with Upstash Redis.

Data: Supabase PostgreSQL with pgvector, Auth, and private Storage.

Graph: Neo4j AuraDB as a projection; PostgreSQL remains authoritative.

Documents: Docling and RapidOCR, with Gemini multimodal fallback.

AI: gemini-2.5-flash-lite plus gemini-embedding-001.

Python execution: self-hosted Judge0, deployed as isolated Railway services.

Hosting: Vercel for web; Railway Hobby for API, worker, and Judge0.

Repository: monorepo; Docker Compose for local dependencies.

## 6. Ownership
Area	Primary owner
Web UI, auth UX, course/learning/progress screens	Member 1
Document processing, RAG, Gemini, curriculum, question generation	Member 2
FastAPI domain/API, migrations, jobs, data, graph projection, Judge0, deployment	Member 3
Primary ownership controls public-contract and migration changes. Other members
may contribute after coordinating with the owner.

## 7. Architecture and security rules
Preserve the domain-module structure under
backend/app/modules/<domain>/ (models.py, router.py, schemas.py,
service.py). New domains get new modules.

Do not create duplicate services, models, routes, or helpers. Reuse existing
implementations.

Every schema change requires an Alembic migration. Never rely on
Base.metadata.create_all for schema evolution.

Never trust client-supplied identity. User identity must come from trusted
server-side authentication/BFF headers via app.core.security.get_current_user.

Return 404, not 403, for resources the caller does not own, so existence is
not leaked.

Keep route handlers thin: authenticate, validate, call a domain service, and
translate the result.

Keep mastery and recommendation engines pure and deterministic.

Put provider SDK calls behind gateway/adaptor interfaces.

Put owner/course filters inside service/repository queries, not only routes/UI.

Make every Celery stage idempotent using durable job/stage/artifact keys.

Publish a course version only after validation; partial drafts remain
diagnostic state.

Build Neo4j projections only from committed PostgreSQL graph versions.

PostgreSQL is authoritative for product state and prerequisite edges;
Neo4j is rebuildable projection state; Redis is for job coordination only.

AI responses must cross typed Pydantic schemas before entering domain state.

Generated artifacts must store model, prompt/schema version, validation state,
and provenance.

All model/provider calls must go through one abstraction that records provider,
model, and prompt version.

Do not silently swallow errors; log structured information around ingestion,
generation, retrieval, grading, and adaptation.

Judge0 receives only bounded code/test payloads and limits; never product
credentials or source files.

## 8. Grounding and untrusted content
Retrieved and uploaded text is data, never an instruction channel.

It must never invoke tools or alter a system prompt.

Search only the authenticated learner’s current course.

Generate from retrieved source context only.

Retain exact chunk provenance internally and show document/page citations.

Validate citation existence, ownership, and semantic support.

Regenerate within the configured bound, then abstain when support remains
insufficient.

No fixed learning-style label may drive adaptation. Adaptation uses per-concept
mastery and per-variant presentation affinity.

## 9. Shared contracts
FastAPI OpenAPI is authoritative for web/API payloads.

Generate TypeScript client/types from OpenAPI; do not maintain parallel
handwritten response types.

PostgreSQL is authoritative for product state and prerequisite edges.

Neo4j is rebuildable projection state.

Redis contains job coordination only.

AI responses cross typed Pydantic schemas before entering domain state.

Generated artifacts store model, prompt/schema version, validation state, and
provenance.

When a shared contract changes:

Update the contract or migration first.

Add/adjust its contract test.

Notify the affected owner before merging.

Regenerate derived clients.

Update dependent code in the same integration window.

Completion criterion: producer and consumer tests pass against the same
versioned contract.

## 10. Frozen simplifications
Implement these choices as specified; document their limitations in tests/debriefs:

One attempt, no hints, no retries.

Binary mastery evidence.

Unrestricted LLM short-answer judgment.

Exact-string numerical grading.

LLM-estimated difficulty.

Generated Python tests are not pre-executed for quality validation.

Prerequisites warn and influence scoring but never hard-block.

Learners cannot override the next activity.

No target dates, schedules, session fitting, spaced review, or forgetting curve.

Upload validation checks extensions only; no malware scanning in P0.

Documents are immutable after course creation.

No automatic AI-provider fallback.

Do not silently improve or change these behaviors inside unrelated tasks. Propose
a scope revision separately.

## 11. Testing and verification
Test the highest public seam available:

Pure unit tests: mastery formula, uncertainty, candidate scoring, tie-breaking,
structural graph checks.

Contract tests: Gemini gateways, Judge0 adapter, Supabase storage/auth, Neo4j
projection, OpenAPI client generation.

Integration tests: REST routes through real local PostgreSQL/pgvector, Redis,
and Neo4j.

Pipeline tests: fixed files plus deterministic Gemini stubs; assert durable
stage transitions and idempotency.

Browser tests: upload/poll/review/diagnostic/learn/progress golden path.

End-to-end acceptance: two unseen supported document sets, one native and one
scanned/visual.

External services stay behind adapters so tests can run without spending quota.
Do not assert private methods, exact prompt prose, or incidental UI structure.

Before marking a task complete:

Run focused tests.

Run affected contract/integration tests.

Run repository format, lint, and type checks.

Run the app/build where practical.

Confirm no secret, prompt content, document content, answer, token, or signed
URL appears in logs or commits.

Report observable behavior, tests run, known limitations, migrations,
environment changes, and downstream unblock.

Never report a check as passing unless it actually ran. Label “confirmed by
inspection” separately from “confirmed by execution.”

## 12. Git and integration workflow
Branch from the current integration branch using
feat/<task-id>-<short-name>.

Keep one task per branch and commit coherent vertical changes.

Pull/rebase the integration branch before handoff; resolve conflicts with the
affected owner.

Member 3 owns migration ordering. Other members request schema changes through
the shared contract before adding migrations.

Merge shared contracts early; merge consumers only after regeneration/tests pass.

Keep main demoable. Integrate through develop during the week and promote
only green checkpoints.

Avoid destructive Git operations and force pushes on shared branches.

Without explicit human approval in the same session, an agent may not:

delete or rewrite a migration;

delete user data;

force-push, rewrite history, or change a remote;

add a dependency introducing a new class of infrastructure;

disable, skip, or weaken a test to make a suite pass.

## 13. Documentation and provider assumptions
Use current official documentation for external APIs/providers when behavior or
limits could have changed. Record consequential provider assumptions in
architecture.md or SYSTEM_ARCHITECTURE.md, not as code comments.

## 14. Definition of done
A change is complete only when:

it does what was asked;

required tests exist and pass where actually run;

lint/type-check pass where run;

migrations exist for schema changes;

no secret is hard-coded;

ownership is enforced server-side;

the report distinguishes executed checks from unexecuted checks;

conflicts with governing documents are explicitly reported; and

the stated pass condition is demonstrated.

## 15. Required task report
After each unit of work, return:

PHASE / TASK:
STATUS: complete | partial | blocked

1. Existing functionality discovered
2. Changes made
3. Files created/modified
4. Database/schema changes (migration IDs)
5. API changes
6. UI changes
7. Tests added/updated, and which layer each belongs to
8. Commands run
9. Test/build/lint results (only what actually ran)
10. Known limitations
11. What the next phase expects to find in place
12. Decisions requiring human approval
13. Conflicts with AGENTS.md / SYSTEM_ARCHITECTURE.md, and how they were handled
