# NeuroLearn — Seven-Day Implementation Plan

Status: frozen execution plan for three team members using AI-assisted coding.

The plan builds one tracer-bullet path first, then deepens it. `frozen-scope.md` owns behavior, `architecture.md` owns boundaries, and `AGENTS.md` owns working conventions.

## Team ownership

| Member | Primary ownership | Integration responsibility |
|---|---|---|
| Member 1 — Web | Next.js UI, Google auth UX, React Flow, learner/admin surfaces | Generated API client and browser golden path |
| Member 2 — Intelligence | Docling/OCR, Gemini gateways, RAG, curriculum and assessment generation | Typed AI schemas and deterministic stubs |
| Member 3 — Platform | FastAPI, domain modules, migrations, Supabase, Celery/Redis, Neo4j, Judge0, deployment | Shared contracts, environments, integration branch |

Each member stays in their primary directories. Shared schema/API changes are integrated at the midday and end-of-day checkpoints.

## Repository shape

```text
apps/
  web/
  api/
  worker/
packages/
  contracts/
  evaluation/
infra/
  migrations/
  docker/
  railway/
docs/                     # optional future home for these root docs
```

## Daily operating rhythm

1. **09:00 sync:** confirm today's contract and unblock dependencies.
2. **09:15–13:00 ownership work:** separate branches; focused tests required.
3. **13:00 integration window:** merge shared contracts/migrations first, regenerate clients, run smoke suite.
4. **14:00–18:00 ownership work:** consume the integrated contract.
5. **18:00 integration window:** merge into `develop`, deploy checkpoint where applicable, run golden-path smoke test.
6. **18:30 debrief:** record tests, failures, environment changes, and next unblock.

No member waits on another member without switching to an unblocked test, fixture, UI state, or adapter stub.

## Definition of done

A task is complete only when:

- Its observable pass condition works.
- Focused tests pass.
- Changed contracts and generated clients agree.
- Required migrations apply from a clean database.
- Errors are represented as product states rather than hidden logs.
- The debrief identifies limitations and downstream dependencies.

## Day 1 — Foundation and risk retirement

### Shared checkpoint D1.0 — Freeze contracts

**Owners:** all; Member 3 integrates.

- Scaffold monorepo and shared environment templates.
- Add root format/lint/test commands and Docker Compose.
- Define course/job status enums and initial OpenAPI conventions.
- Add CI for web typecheck/test and Python lint/test.

**Pass:** one command starts local dependencies; one command runs all empty-project quality gates; web can call API `/health`.

### T101 — Web shell and auth boundary

**Owner:** Member 1. **Depends on:** D1.0.

- Build the application shell and protected route boundary.
- Integrate Supabase Google sign-in using identity scopes only.
- Add setup, course, learning, progress, and admin route placeholders with real auth state.

**Pass:** signed-out users reach sign-in; signed-in users reach protected shell; another user's mocked course URL is rejected by the API rather than hidden only in UI.

### T102 — API, database and ownership foundation

**Owner:** Member 3. **Depends on:** D1.0.

- Scaffold FastAPI, Pydantic settings, SQLAlchemy, Alembic, and Supabase JWT verification.
- Create initial user, course, document, job, stage, and version tables.
- Implement `/health`, `/health/db`, `/me`, and course create/list/get/delete.
- Enforce owner filters in repository/service queries.

**Pass:** clean migrations succeed; database health is green; two test users cannot access each other's course.

### T103 — Judge0-on-Railway spike

**Owner:** Member 3. **Starts immediately; highest risk.**

- Map official Judge0 server, workers, PostgreSQL, and Redis containers to Railway services.
- Deploy a minimal isolated environment.
- Submit a Python function and receive a result.
- Prove the execution payload has no application secrets and no public network access.

**Pass:** deployed HTTP submission returns correct result for one passing and one failing Python function. If red by end of Day 1, escalate as the only P0 infrastructure blocker.

### T104 — Typed Gemini gateway and fixtures

**Owner:** Member 2. **Depends on:** D1.0.

- Define generation, multimodal, embedding, and validation gateways.
- Pin `gemini-2.5-flash-lite` and `gemini-embedding-001`.
- Implement Pydantic schema rejection, safe metadata logging, quota/unavailable errors, and deterministic test adapters.

**Pass:** valid fixtures deserialize; malformed model output is rejected; tests confirm source content is absent from logs.

### Day 1 integration gate

- Web authenticates against API.
- API persists an owner-scoped empty course.
- Gemini adapter contract passes with stubs and one real smoke call.
- Judge0 spike is green or formally the top Day 2 blocker.

## Day 2 — Upload, jobs, extraction and storage

### T201 — Course setup and upload UI

**Owner:** Member 1. **Depends on:** T101, T102 contracts.

- Build goal, confidence, priorities/exclusions, syllabus, and study-file form.
- Enforce visible format/count/size/page expectations.
- Upload through private signed intents and finalize the immutable source set.
- Build processing-stage polling UI with retryable failure state.

**Pass:** a signed-in learner uploads a syllabus and study file, sees the durable job, leaves, returns, and resumes polling.

### T202 — Private object and job orchestration

**Owner:** Member 3. **Depends on:** T102.

- Configure private Supabase bucket and signed upload/read flows.
- Implement upload metadata, extension checks, source finalization, and immutability.
- Configure Upstash Redis and Celery.
- Implement durable job/stage state, enqueue, claim, idempotency key, polling, and manual retry.

**Pass:** duplicate delivery does not duplicate document or stage records; browser disconnect does not stop processing; another user cannot sign an object URL.

### T203 — Document normalization pipeline

**Owner:** Member 2. **Depends on:** T104 and T202 adapter contract.

- Normalize PDF, images, TXT, Markdown, DOCX, and PPTX through Docling.
- Route printed scans through RapidOCR.
- Route uncertain visual/handwritten regions through multimodal Gemini.
- Persist page text, headings, tables, code, asset references, interpretations, confidence, and provenance.
- Surface critical versus non-critical uncertainty.

**Pass:** one native PDF, one DOCX/PPTX, and one scanned/visual fixture create a unified page/asset representation; a critical extraction failure becomes `NEEDS_INPUT` or `FAILED` without publishing a course.

### Day 2 integration gate

An authenticated unseen upload reaches durable extracted pages through the worker, and the web shows every stage/error from API state.

## Day 3 — Retrieval, curriculum and graph

### T301 — Course review and graph UI

**Owner:** Member 1. **Depends on:** draft structure contract.

- Render module/lesson/concept hierarchy.
- Render prerequisite graph in React Flow.
- Support title, placement, and edge edits plus low-confidence indicators.
- Block publication on visible cycle/critical-gap errors.

**Pass:** learner edits a concept and edge; refresh preserves the draft; a cycle is shown and cannot be published.

### T302 — Retrieval index and grounded-answer seam

**Owner:** Member 2. **Depends on:** T203.

- Build structure-aware chunks and Gemini embeddings.
- Store vectors in pgvector with owner/course/document/page metadata.
- Implement lexical + vector candidate retrieval, metadata filtering, deduplication, and reranking.
- Implement course-only grounded generation, document/page citations, citation support validation, bounded regeneration, and abstention.

**Pass:** an answer retrieves only current-course chunks; a cross-course bait document is never returned; an unsupported question abstains; citation validation rejects a fabricated page/chunk.

### T303 — Curriculum persistence and graph projection

**Owner:** Member 3. **Depends on:** T202 and Member 2 schemas.

- Add course hierarchy, concept, edge, gap, artifact, citation, and graph-version migrations.
- Implement draft structure CRUD and publication transaction.
- Validate unique syllabus mapping and cycles.
- Project published PostgreSQL edges into Neo4j idempotently and report projection status.

**Pass:** published graph traverses in Neo4j; deleting the projection and rerunning rebuilds the same graph from PostgreSQL.

### T304 — Syllabus, concepts and prerequisites

**Owner:** Member 2. **Depends on:** T203, T302.

- Use uploaded syllabus, detected table of contents, or automatic syllabus generation in that priority.
- Generate modules, lessons, independently assessable concepts, source provenance, confidence, content gaps, and prerequisite edges.
- Run structural validation and separate AI validation.

**Pass:** an unseen source set yields a schema-valid draft with every syllabus topic mapped or marked as a content gap; cycles prevent readiness.

### Day 3 integration gate

Upload one unseen document set and reach an editable, source-provenanced, published course graph with a working grounded-answer API.

## Day 4 — Diagnostic, mastery and deterministic adaptation

### T401 — Diagnostic and learning workspace UI

**Owner:** Member 1. **Depends on:** assessment/activity contracts.

- Build optional diagnostic start/skip flow.
- Render one-shot questions and post-submit feedback.
- Build guided activity shell, citation viewer, grounded side-chat, and presentation rating.
- Build locked next-activity transition; no arbitrary course navigation.

**Pass:** diagnostic skip shows unknown state; answering advances to feedback once; next activity is loaded from the API, not selected in UI.

### T402 — Mastery and uncertainty engine

**Owner:** Member 3. **Depends on:** assessment schema.

- Add mastery state/event and attempt persistence.
- Implement unknown state, binary evidence update, difficulty factor, independent-evidence uncertainty reduction, and completion thresholds.
- Record response duration without using it in mastery.

**Pass:** table-driven tests cover unknown, correct, incorrect, repeated evidence, threshold completion, and early-ended course gaps.

### T403 — Recommendation engine

**Owner:** Member 3. **Depends on:** T402 and published graph.

- Implement fixed activity enum, candidate generation, prerequisite warnings, scoring features, and fixed tie-breaking.
- Persist candidate scores, selected activity, reason features, and rule version.
- Add predefined simulated learner fixtures.

**Pass:** every frozen simulation returns the expected activity deterministically; identical state always produces identical decision data.

### T404 — Diagnostic and lesson generation

**Owner:** Member 2. **Depends on:** T302, T304.

- Generate a maximum-15-question adaptive diagnostic blueprint.
- Generate/cache source-grounded activities across supported presentation formats.
- Implement grounded side-chat and presentation-affinity update inputs.

**Pass:** generated diagnostic covers foundational/important concepts; lesson claims cite owned sources; unsupported chat abstains.

### Day 4 integration gate

Published course → diagnostic/skip → grounded activity → persisted attempt → mastery change → deterministic next activity works through API and web.

## Day 5 — All assessment types, progress and traces

### T501 — Assessment and progress UI

**Owner:** Member 1. **Depends on:** all assessment response contracts.

- Render MCQ, short answer, exact-string numerical, and Python editor/submission.
- Show one-attempt state and feedback.
- Build mastery/uncertainty progress, syllabus gaps, completion state, and activity history.
- Build evaluator trace tables and drill-down shell.

**Pass:** each type submits once and renders durable feedback after refresh; learner sees only personal data; admin-only trace route rejects learners.

### T502 — Question generation and validation

**Owner:** Member 2. **Depends on:** T302.

- Generate assessment blueprints and on-demand cached questions.
- Implement separate same-model validation for source support, answerability, expected answer, difficulty, and duplication.
- Implement unrestricted LLM short-answer binary judgment and generated pre/post equivalence validation.
- Create generated Python prompts/tests without reference execution, matching frozen scope.

**Pass:** malformed/unsupported questions never publish; failed bounded generation marks assessment unavailable; cached versions are reused.

### T503 — Grading and Judge0 adapter

**Owner:** Member 3. **Depends on:** T103 and T502 contracts.

- Implement MCQ comparison, exact-string numerical comparison, LLM judgment result ingestion, and Judge0 Python submission.
- Enforce one attempt at database/API boundary.
- Bound Python time, memory, output, and network; never transmit application credentials.
- Connect every result to mastery evidence and adaptation outcome.

**Pass:** second attempts are rejected; passing/failing Python functions return binary outcomes; Judge0 outage leaves mastery unchanged and question unavailable.

### T504 — Outcome and affinity linkage

**Owner:** Member 3 with Member 2 schema review. **Depends on:** T402, T403, T502.

- Persist optional three-state rating.
- Update format affinity from rating and subsequent correctness using a versioned weighted moving average.
- Link adaptation decision to the next relevant assessment and mastery delta.

**Pass:** trace reconstructs recommendation → activity → attempt → mastery delta; unrelated attempts do not update the wrong format or decision.

### Day 5 integration gate

All four assessment types execute real paths; progress and protected decision/outcome trace update correctly.

## Day 6 — Evaluation, deletion, deployment and full integration

### T601 — Evaluation dashboard

**Owner:** Member 1. **Depends on:** evaluation APIs.

- Build admin-only system metrics, simulated-profile results, participant drill-down, performance tables, and security-event categories.
- Show automated metrics with explicit labels and concept/multimodal claim limitations.

**Pass:** learner receives forbidden response; admin can inspect pseudonymized records without participant email exposure.

### T602 — Evaluation engine

**Owner:** Member 2. **Depends on:** T302, T502.

- Implement generated pre/post blueprints and separate equivalence validation.
- Calculate participant-level post-minus-pre result only.
- Calculate automated supported-claim precision and unsupported-claim rate from validator labels.
- Add structural graph and subjective multimodal result schemas.

**Pass:** evaluation output never pools participant learning scores and uses the frozen claim wording/labels.

### T603 — Platform hardening and deletion

**Owner:** Member 3. **Depends on:** all domain tables.

- Add per-user quotas, safe error categories, admin role, and account deletion cascade/jobs.
- Verify private objects, signed URLs, owner/course filters, prompt tool isolation, and safe logging.
- Add prompt-injection, cross-user retrieval, and Judge0 isolation tests.

**Pass:** deletion removes all product/pilot records and objects; cross-user suite is green; secrets/content are absent from logs.

### T604 — Production deployment

**Owner:** Member 3; Members 1/2 supply configs. **Depends on:** platform services.

- Deploy web to Vercel.
- Deploy API, Celery worker, and decomposed Judge0 to Railway Hobby.
- Connect Supabase, Upstash, Neo4j, and Gemini secrets.
- Apply migrations, configure CORS/OAuth redirects, health checks, quotas, and safe environment separation.

**Pass:** public URL completes sign-in and a smoke upload; API/worker/Judge0 health checks pass; no localhost dependency exists.

### Day 6 integration gate

The complete golden path works in production on one non-seeded native-text document set.

## Day 7 — Unseen acceptance, fixes and presentation evidence

### T701 — Unseen native document acceptance

**Owners:** all.

- Select a supported native-text set unknown to the implementation.
- Run the full golden path without code changes.
- Capture stage timings, failures, trace, citations, and final mastery/recommendation.

**Pass:** every step in the week-one pass condition completes.

### T702 — Unseen scanned/visual acceptance

**Owners:** all.

- Select a supported set with at least one scanned or visual page.
- Run the identical pipeline without special configuration.
- Record subjective extraction usefulness and known limitations.

**Pass:** every golden-path stage completes or a documented critical-uncertainty path requests correction without fabricating content; final rerun completes after allowed input correction.

### T703 — Focused hardening

**Owners:** assigned by failing subsystem.

- Fix only failures that block T701/T702, ownership isolation, data integrity, or deployment.
- Preserve frozen simplifications; move polish and broad reliability to post-week work.

**Pass:** full CI and both acceptance sets are green twice consecutively.

### T704 — CA-2 evidence package

**Owner:** all; one presenter integrates.

- Map every frozen requirement to architecture component and acceptance evidence.
- Finalize architecture and database diagrams.
- Present technology justifications and explicit limitations.
- Demonstrate database connectivity, processing status, course graph, grounded citation, mastery update, and trace.

**Pass:** the presentation explicitly covers all rubric rows: requirements-to-design mapping, architecture/database connectivity, and tool/technology justification.

## Integration order

When parallel branches meet, merge in this order:

1. Database migration and domain enum.
2. FastAPI/Pydantic request/response contract.
3. OpenAPI export and generated TypeScript client.
4. Provider adapter or worker implementation.
5. Web consumer.
6. Cross-service integration test.

This order prevents UI, worker, and API branches from inventing incompatible shapes.

## Shared environment variables

Document exact names in `.env.example`; never commit values. Categories:

- Supabase URL, anon key, service key, PostgreSQL URL, private bucket.
- Google OAuth client configuration through Supabase.
- Upstash Redis connection.
- Neo4j Aura URI and credentials.
- Gemini API key and explicit generation/embedding model IDs.
- Judge0 internal URL and authentication token.
- Web/API public origins and admin allowlist.
- Per-user quotas and generation retry limits.

## Required test matrix

| Behavior | Unit | Contract | Integration | Browser/E2E |
|---|---:|---:|---:|---:|
| Ownership isolation |  |  | Yes | Yes |
| Job state/idempotency | Yes |  | Yes | Yes |
| Document normalization |  | Yes | Yes |  |
| Course-scoped retrieval |  | Yes | Yes | Yes |
| Citation validation/abstention | Yes | Yes | Yes | Yes |
| Graph publication/projection | Yes | Yes | Yes | Yes |
| One-attempt grading | Yes | Yes | Yes | Yes |
| Mastery/uncertainty | Yes |  | Yes | Yes |
| Deterministic recommendation | Yes |  | Yes | Yes |
| Judge0 isolation |  | Yes | Yes | Yes |
| Account deletion |  |  | Yes | Yes |
| Two unseen source sets |  |  |  | Yes |

## Scope-control rule

During the seven days, new ideas enter a post-week list unless they are required to:

1. Complete the frozen golden path.
2. Protect ownership or data integrity.
3. Make the architecture truthful.
4. Satisfy the CA-2 rubric.

Everything else waits.
