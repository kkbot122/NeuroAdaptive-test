# NeuroLearn — Sprint Log

Running record of the autonomous 2-day build. Newest entries at the bottom.
Each entry: what was built, what was decided and why, what was skipped, the
commands actually run, and what the next item needs.

---

## 2026-08-28 — STEP 0: reconciliation

### Planning docs

All four were present in the working tree but **untracked**, so nothing
inspecting the repository could see them. Now committed — `frozen-scope.md` and
`implementation-plan.md` in `e1343f9`, `architecture.md` separately afterwards.

Correction to `e1343f9`: its message claims it added architecture.md, and it
did not. The file sat on disk as `ARCHITECTURE.md`, `core.ignorecase` is true,
and `git add architecture.md` silently staged nothing while the commit
succeeded on the other two files. Committed properly under the lowercase name
AGENTS.md §2 references. Caught by a `git status` check, not by the commit.

`AGENTS.md` had been overwritten by a paste carrying chat-UI artifacts
("AGENTS(1).md / File", "give one common agents.md file", "Meet Codex in the
desktop app", "Download the app") and had lost every markdown heading, so it
had no navigable structure. Repaired in `dfcf515`: artifact preamble stripped,
15 section headings restored, body text otherwise byte-identical. No rule was
added, removed, or reworded.

`architecture.md` and `SYSTEM_ARCHITECTURE.md` both exist. Not a conflict:
AGENTS.md §2 reads "architecture.md **or** SYSTEM_ARCHITECTURE.md". Retained
both — architecture.md carries the frozen target topology, SYSTEM_ARCHITECTURE.md
carries verified current state and the K-1..K-14 issue tracker.

### The repo snapshot in the mandate is stale in four places

Verified directly rather than trusted, as instructed:

| Mandate says | Actually true |
|---|---|
| `app/core/archetypes.py` (six-way archetype system) exists | **Deleted** (`fc66643`). Zero importers at the time. |
| `services/adaptation.py` is a rule-based four-way archetype transform | Already rewritten as a continuous dot-product directive engine; its docstring reads "No archetypes. No labels." Four labels survive only in a back-compat lookup. |
| `app/modules/profiling` is a live FSLSM router | Only `models.py` survives (it owns `UserProfile`). Router and schemas deleted; the router was never registered. |
| "compose comment claims the internal-token fallback was removed — verify" | **Verified genuinely gone.** The only `dev_secret_key_123` occurrence left is inside `_KNOWN_INSECURE`, the validator list that *rejects* that value. Covered by 16 tests. |

Confirmed accurate: no Course/Document/Chunk/Concept/Mastery/AdaptationDecision
anywhere; compose defines only `db`, `backend`, `frontend`; `qdrant-client`,
`boto3`, `minio` are declared dependencies imported nowhere.

### Blocker identified, not yet blocking

**No Gemini API key is configured.** `backend/.env` has `SECRET_KEY`,
`DATABASE_URL`, `GOOGLE_CLIENT_ID/SECRET`, `FRONTEND_URL`, `INTERNAL_API_KEY`,
`GROQ_API_KEY` — no `GEMINI_API_KEY`. This blocks the embed step of item 2 and
everything downstream. It does not block item 1, so item 1 was built first.

### Mastery formula: ambiguity noted, not yet due

Neither frozen-scope.md nor architecture.md contains the actual weighted-evidence
formula — both refer to "the paper's" update and specify only its *properties*
(unknown start, binary correctness + LLM difficulty as factors,
independent-evidence uncertainty reduction, 0.80/0.30 thresholds). Not raised as
a blocker now because item 5 is distant and a defensible default exists
(`SYSTEM_ARCHITECTURE.md` §10). Will confirm before implementing item 5.

---

## 2026-08-28 — Item 1: Foundation — COMPLETE

### Built

- **`courses` module** — `Course` model, service, schemas, router.
  `POST/GET/PATCH/DELETE /api/v1/courses`, plus
  `POST /courses/{id}/finalize-sources`.
- **`documents` module** — `Document` model (upload path lands in item 2).
- **`jobs` module** — `ProcessingJob` + `ProcessingStage`, with the
  frozen-scope stage enum and an `ACTIVE_STAGE_ORDER` list.
- **`identity` module** — `GET /api/v1/me`, `GET /health`, `GET /health/db`.
- **Migration `c4a81b26df57`** — four tables, UUID primary keys, chained to
  `b7d3e91f4c02`. Chain remains single-headed.

### Decisions

- **Ownership lives in the service layer, not the router.** `CourseService`
  puts `owner_id` inside the query itself, so a route that forgets to check
  still cannot return another learner's course. `get_owned()` is the single
  accessor other modules must use.
- **404, never 403, for another user's course.** 403 confirms existence.
  A test asserts the response for someone else's real course is byte-identical
  to the response for a UUID that was never created.
- **`owner_id` omitted from `CourseOut`.** The caller is always the owner, so
  it adds nothing and leaks an internal identifier.
- **`Document.owner_id` denormalised** from `courses.owner_id` so ownership is
  enforceable in one query without a join on every retrieval path.
- **`INTERPRETING_VISUALS` kept in the stage enum but excluded from
  `ACTIVE_STAGE_ORDER`.** The frozen substitutions rule out OCR and multimodal
  this sprint. Keeping it declared but skipped is more truthful than silently
  omitting a stage the target pipeline has.
- **Built alongside `Article`/`Paragraph`, not on top of it.** Nothing in the
  new path imports or queries the legacy content model, the FSLSM vectors, or
  the archetype bridge, per the mandate.

### Skipped deliberately

Document upload itself (item 2). Course versioning, modules/lessons/concepts
(item 3). Nothing in `courses` writes a `ProcessingJob` yet.

### Commands run

```
pytest                    -> 140 passed
```

New this item: 26 tests — `test_course_ownership.py` (22) and
`test_health_and_me.py` (4). The isolation tests use two real users and cover
every verb on the course surface.

### What item 2 needs from this

`Course.finalize_sources()` sets status `PROCESSING` and is the natural trigger
point for enqueuing a `ProcessingJob`. `Document.storage_path` and
`needs_input_reason` are in place for the upload and extraction stages.
`ACTIVE_STAGE_ORDER` is the sequence the in-process runner should walk.

---

## 2026-08-28 — Item 2: Upload and ingestion — PARTIAL (blocked at embedding)

### Built

- **Upload** — `POST /courses/{id}/documents`. One syllabus + two study
  files, PDF/TXT/Markdown, 25 MB. Rejected before enqueue on extension, size,
  emptiness, per-role count, and after sources are finalized.
- **Storage** — backend-local disk under a generated filename. Only read path
  is `GET /documents/{id}/content`, owner-checked per request.
- **Extraction + chunking** — `documents/extraction.py`. Pure functions, no
  DB/network/model. Chunks carry heading path, page range, and content type;
  code fences are never split.
- **Job runner** — in-process, writing real per-stage rows.
  `GET /jobs/{id}` polls, `POST /jobs/{id}/retry` resumes.
- **Qdrant** added to docker-compose with a persistent volume.

### Decisions

- **Pause, don't fail, at an unimplemented stage.** The runner marks the job
  `PAUSED` with a retryable category and leaves completed stages `SUCCEEDED`.
  This is the same behaviour frozen-scope.md specifies for provider
  unavailability — which is exactly what the missing Gemini key is — so the
  pipeline resumes on retry instead of restarting.
- **Chunking is idempotent** by deleting a document's own prior chunks before
  rewriting. A retry cannot produce a second set.
- **`owner_id` and `course_id` denormalised onto `chunks`** so the retrieval
  filter is applied inside the query, as the mandate requires, rather than by
  joining through `documents` on every search.
- **Stored filenames are generated.** A learner-supplied name never decides a
  path on disk; a test uploads `../../escape.txt` and asserts the stored path
  contains no traversal.
- **NEEDS_INPUT names scans specifically.** A valid PDF with no selectable text
  gets a learner-facing reason, not an empty result and not a "corrupt file"
  error. Tested with a real textless PDF built via `PdfWriter`, after a
  hand-written one turned out to be malformed enough to fail at open.

### Unplanned but necessary: the migration chain could not build a database

Discovered while checking that `chunks` had a migration. Three faults, all
verified by running the chain, all pre-existing:

1. `2f4c2d25f29c` **dropped** `articles`/`paragraphs`, which no upgrade ever
   created — autogenerated against a create_all-populated database.
2. `86bda7902ec8`, named "add chat sessions and reading logs", has `pass` for
   an upgrade. `chat_sessions`, `chat_messages`, `article_readings` had **no
   migration at all**.
3. `6963bcb15db5` changed nullability outside batch mode, so the chain could
   not run on SQLite and could not be verified locally.

This was latent until the K-3 fix removed `create_all` and made Alembic the
only schema mechanism — so it is a consequence of my own earlier change, and
fixing it was not optional. `alembic upgrade head` on an empty database now
produces all 14 model tables. Verified on SQLite; **not** verified on
PostgreSQL, because Docker is not available in this environment.

### Skipped deliberately

Embedding, Qdrant indexing, and everything downstream. No frontend polling UI
yet — the API contract it needs (`GET /jobs/{id}` with per-stage status) is in
place.

### Commands run

```
pytest                  -> 174 passed
alembic upgrade head    -> clean, on an empty SQLite database, 14/14 tables
```

### BLOCKED — what I need from you

**A Gemini API key.** Add to `backend/.env`:

```
GEMINI_API_KEY=<key from https://aistudio.google.com/apikey>
GEMINI_GENERATION_MODEL=gemini-2.5-flash-lite
GEMINI_EMBEDDING_MODEL=gemini-embedding-001
```

Everything from the INDEXING stage onward needs it: embedding chunks, concept
extraction, curriculum generation, grounded lessons, question generation.
Without it the pipeline correctly stops at `PAUSED` after CHUNKING.

---

## 2026-08-28 — Out of band: Groq model retirement (branch `fix/groq-model-deprecated`)

Not part of the numbered scope. Every chat request was returning 404:
`The model llama-3.3-70b-versatile does not exist or you do not have access to
it`. Groq has retired it — confirmed against the live API, which now offers 14
models and not that one.

Two faults, fixed separately:

1. **Hardcoded model id** at two call sites, so a provider retirement meant a
   code change in two files. Now `settings.GROQ_MODEL`, defaulting to
   `openai/gpt-oss-120b` (verified with a real completion on this account).
2. **The provider's raw error was shown to the learner** — JSON naming our
   model and the provider's internal error codes. Now logged server-side with
   type/status/model; the caller gets a category. Status 500 → 502, since the
   API is healthy and its upstream is not.

Worth recording: while probing replacements, `gpt-oss-*` returned empty content
at `max_tokens=12`. That is truncation, not incapacity — they emit reasoning
tokens before output and answer normally at a realistic budget. A shallower
check would have wrongly ruled them out.

Also worth recording: the first version of the tests patched
`app.services.adaptation.get_client`, but the router imports that function into
its own namespace, so the patch never applied and the tests were silently
hitting the real Groq API. They only passed for the 401 case, by accident,
because the fake test key produced the same status. Patched at the point of use.

Verified live: `POST /api/v1/chat/message` against the running stack with a
real user returns a grounded-format answer. Suite at 185.

**This does not change the RAG position.** Chat still answers from model
weights: no retrieval, no citations, nothing indexed. Qdrant holds 0
collections and `chunks` holds 0 rows.

---

## 2026-08-29 -- Phase 1 pack: foundation hardening + ingestion/RAG foundation

An externally-supplied "Phase 1" prompt pack, from the original 11-phase
NeuroLearn plan that predates the frozen-scope pivot. Reconciled against the
governing docs per AGENTS.md's authority order before writing any code.

### Two conflicts declared, not silently resolved

**Auth architecture.** The pack wants a homegrown backend-issued JWT plus a
rotating refresh cookie, replacing the shared-secret header pattern.
architecture.md names Supabase Auth as the frozen identity provider -- a
third, different architecture from both the current model and what the pack
proposes -- and the original build mandate for this branch explicitly said
"switching identity providers is not on the critical path to a demo."
Building custom JWT infrastructure now would be throwaway work against the
frozen target and would spend sprint time the mandate earmarked elsewhere.
**Resolution: the frozen docs win.** No JWT/refresh work was done. The
shared-secret model already has no fallback default and a validated minimum
length (K-1, closed 2026-08-27); that stands as the P0 posture, with K-4
(header-trust) still tracked open for Stage 4.

**Pipeline stage vocabulary.** The pack specifies
verify/scan/extract/normalize/chunk/embed/index/quality_check/ready.
frozen-scope.md's own pipeline (already implemented as
`ProcessingStageName`) is VALIDATING/EXTRACTING/INTERPRETING_VISUALS/
CHUNKING/INDEXING/EXTRACTING_CONCEPTS/BUILDING_GRAPH/GENERATING_STRUCTURE/
VALIDATING_COURSE. **Resolution: kept the frozen names.** Embedding and
indexing both happen inside the existing INDEXING stage rather than
introducing a second, conflicting vocabulary.

### Built

**Chunking rework** (already logged in the previous entry's follow-on
commit): token-based sizing (tiktoken cl100k_base, target 650/cap 800/overlap
75 -- midpoints of the pack's 500-800/50-100 ranges), char offsets into the
document's concatenated text, deterministic chunk ids
(`uuid5(document_id, extraction_version, position)`), upsert-in-place instead
of delete-then-reinsert.

**Upload hardening:**
- Magic-byte sniffing (`documents/magic_bytes.py`) -- dependency-free
  signature check rather than python-magic/libmagic, since the supported
  format set is exactly {pdf, txt, md} and a handful of known signatures
  cover it completely without an OS package.
- SHA-256 checksum dedup, scoped per-course: an identical re-upload returns
  the existing document (200, not 201) instead of storing and reprocessing a
  duplicate, and does not count against the per-role file cap.
- Pasted text as a fourth ingestion path
  (`POST /courses/{id}/documents/paste`), written to disk as a .txt so it
  needs no separate extraction/chunking code path.

**Embedding + retrieval (the actual RAG foundation):**
- `EmbeddingGateway` abstraction with a `GeminiEmbeddingGateway`
  (gemini-embedding-001, 3072 dims -- verified against the live API) and a
  deterministic `FakeEmbeddingGateway` for tests.
- `VectorStore` abstraction with a `QdrantVectorStore` and an in-memory
  `FakeVectorStore` that implements real cosine similarity, so a test against
  it exercises the same filter-before-rank contract real Qdrant provides.
- `INDEXING` stage now actually embeds and upserts: each chunk's heading path
  is prepended before embedding (retrieval quality) while the stored
  `chunk.text` stays exactly the source text (citation + inert-data
  guarantee). Batched, idempotent by chunk id.
- Lexical search (`retrieval/lexical.py`): PostgreSQL `to_tsvector`/`ts_rank`
  computed on the fly (no stored column, no GIN index -- reasonable at this
  corpus size and avoids a Postgres-only column the SQLite test schema can't
  express); a Python term-overlap fallback on SQLite preserves the identical
  ownership-filter-in-SQL property for the test suite, differing only in
  ranking, not in what it's allowed to return.
- `RetrievalService`: verifies course ownership once via the same
  `CourseService.get_owned` every other module uses, then applies owner/course
  filtering INSIDE both the vector query and the lexical query -- never as a
  post-filter -- with a third, defense-in-depth re-filter on the final
  hydration query. `GET /courses/{id}/retrieval?q=...`.

### The headline test

`test_isolation_holds_even_when_the_other_users_content_is_more_relevant`:
seeds another user's course with text that scores far higher against the
query than anything in the caller's own course, then asserts zero cross-over.
This is the structural claim the mandate asks for, not a weaker "returns the
right rows" check.

### A design decision worth flagging: fakes became the default for the whole suite

Wiring INDEXING to actually call embeddings/Qdrant meant the *existing*
`client` test fixture -- used by every prior test file -- started making real
network calls the moment any test walked a job to INDEXING, since it had no
injected fakes. Two tests in test_ingestion.py written before INDEXING
existed broke as a result, one exposing a real problem: the suite went from
~17s to 68s, because `settings.QDRANT_URL`'s default (the Compose service
name) doesn't resolve at all outside the container network, so every such
test was burning a full connection-timeout.

Fixed at the root rather than per-test: `tests/conftest.py`'s shared `client`
fixture now overrides the job and retrieval service factories with
`FakeEmbeddingGateway`/`FakeVectorStore` for every test, not just the new
retrieval file. No test in this suite may depend on a reachable Gemini key or
a running Qdrant instance. Suite is back to ~16s.

### Bugs the tests caught before they shipped

- Sentence-boundary sub-splitting for an oversized single block initially
  double-counted: two ~650-token pieces would both get appended before the
  target-crossing check tripped, producing a ~1300-token chunk. Fixed by
  flushing immediately after each already-target-sized sub-piece.
- SQLite's `Uuid` column type requires real `uuid.UUID` bind values, not
  strings -- `Chunk.id.in_(all_ids)` failed until `all_ids` (built from
  string dict keys) was converted back to `UUID` before the query. Same class
  of bug appeared three more times in test code that filtered by a JSON
  response's string course id directly.
- A heredoc-based edit earlier in this session had written literal NUL bytes
  into test_ingestion.py where `\x00` escapes were meant as source text,
  which broke pytest collection outright until the file was rebuilt from the
  last clean commit.

### Verified live

Docker image rebuilt (tiktoken and real qdrant-client usage are new since
the last build). `alembic upgrade head` re-verified against real PostgreSQL
after the chunk-provenance and document-checksum migrations.
`gemini-embedding-001` and `openai/gpt-oss-120b` calls verified against the
live keys outside the app, before wiring them in, per this session's standing
practice of not trusting an SDK's behaviour without an empirical check.

### Not done -- Part A items outside this reconciliation

RFC 7807 problem-details bodies, cursor pagination, and Idempotency-Key/
If-Match support are not implemented. Small in isolation but touch every
existing route; deferred rather than rushed across 30+ endpoints in this
pass. Left for a dedicated pass rather than silently dropped.

Suite: 242 passing.

---

## 2026-08-29 -- Real end-to-end verification against the live stack

The RAG foundation commit above was verified against real infrastructure,
not just the fake-provider test suite. Docker image rebuilt (tiktoken and
real qdrant-client usage are new since the last build).

**Real quota problem found and fixed.** Processing the actual 130-chunk,
92-page OS PDF against live Gemini paused with `EmbeddingError` on the first
attempt: a batch of 50 realistic-sized chunks in one embed_content call
raised `ResourceExhausted`, and testing smaller sizes showed even repeated
batches of 20 failed after the first call succeeded. This is exactly the
provider-quota scenario frozen-scope.md names ("pauses the job for manual
retry; there is no automatic provider fallback") and the pipeline correctly
paused rather than corrupting state or silently producing partial chunks.
For the pipeline to actually finish on real documents, added bounded
retry-with-backoff (3 attempts, 10/20/40s) on the same provider -- ordinary
resilience against a transient, expected limit, not a fallback to a
different one -- and dropped the batch size to 10, verified stable through
three consecutive real calls. Writing the regression test for this exposed a
dead `for/else` branch: the "failed after N retries" message was
unreachable because the final attempt's exception was already raised inside
the loop body.

**Full real run, after the fix:**

```
Upload:    92-page OS lecture-notes PDF (never used elsewhere in this repo)
Chunking:  130 chunks, real heading paths and page ranges
INDEXING:  SUCCEEDED -- 130/130 chunks embedded via live gemini-embedding-001
           and upserted into a real Qdrant collection (verified via
           /collections/course_chunks: points_count=130, vector size=3072)
Pipeline:  paused at EXTRACTING_CONCEPTS (Phase 2, correctly unimplemented)
Query:     "what is a deadlock" -> top result is the actual "Deadlocks"
           section, page 43, score 0.712, both vector and lexical agreeing
Isolation: a real unauthorized email against the real course -> HTTP 404
```

This is the first point in the build where a genuinely unseen document goes
all the way from upload to a correct, cited, ownership-scoped answer to a
real question -- the actual research claim, exercised for real rather than
against fakes.

Suite: 251 passing (9 new retry-behaviour tests).

---

## 2026-08-29 — Phase 2: concept graph and curriculum generation

An externally-supplied "Phase 2" prompt from the original 11-phase pack.
Reconciled against the governing docs before writing code, same discipline
as Phase 1.

### Two conflicts declared, not silently resolved

**Hard prerequisites that gate access.** The pack asks for hard edges that
"gate readiness" (block a dependent concept) versus soft edges that only
influence scoring. frozen-scope.md is explicit and repeated: "Prerequisite
weakness produces a warning and influences scoring but does not block access
to a dependent concept" and "Prerequisites always remain eligible." There is
no gating prerequisite in the frozen product. **Resolution: frozen-scope
wins.** `EdgeStrength.HARD/SOFT` is stored as metadata (feeding the
readiness score's magnitude per the paper's R(c) = min over prerequisites),
but no code path may use it to block anything — documented in
curriculum/models.py's module docstring, the actual enforcement point.

**API naming.** The pack's test cases reference `PATCH /courses/{id}/outline`
and `GET /courses/{id}/concept-graph`. architecture.md's own API surface —
which states "the API specification is authoritative" — names
`GET/PUT .../structure`, `GET/PUT .../graph`, `POST .../publish-structure`.
**Resolution: architecture.md's naming used.** The behaviour the pack's
tests actually check (a rename persists and is reflected on the next read;
a graph is owner-scoped) is implemented and tested under the frozen names.

### Built

Own domain module (`curriculum/`) per AGENTS.md's ownership rule — the
first phase adding genuinely new modelling rather than extending Phase 1's
ingestion domain.

- **Models**: CourseVersion (immutable, versioned), Concept, ConceptSource,
  ConceptPrerequisite, Module, Lesson, LessonConcept, AssessmentBlueprint.
  Four migrations, the last three of which corrected the same design gap
  found mid-build: Concept and ConceptPrerequisite were initially scoped to
  course_id only, which would have mixed every prior version's concepts into
  one pile on regeneration. Added course_version_id before building anything
  on top of the gap.
- **graph.py** — DFS cycle detection and resolution (no networkx). Soft
  edges get no acyclicity exemption; a cycle is a cycle.
- **normalization.py** — three-band embedding-similarity decision
  (auto-merge / LLM-adjudicate / keep distinct) plus deterministic
  `canonical_key()` for cross-version matching.
- **extraction.py** — one bounded-context LLM call per document section
  (grouped by heading_path), never a whole-document prompt.
- **edges.py** — one LLM call over the normalized concept list proposing
  prerequisite edges; hallucinated names and self-edges are dropped.
- **validation.py** — the deterministic gate: every concept has a lesson,
  every ConceptSource resolves to a real owned chunk, the graph is acyclic,
  every important concept has assessment coverage.
- **carryover.py** — canonical_key match first, embedding similarity second
  (0.90 floor), otherwise "new" rather than a guessed match.
- **service.py** (CurriculumService) — orchestrates all of the above with
  real persistence. Module/lesson clustering and assessment blueprinting are
  deterministic and heuristic this phase (grouped by the source document's
  own heading structure; one MCQ per important concept), not LLM-driven —
  a scope simplification, not an attempt at the mandate's full ambition.
- **Job pipeline integration**: wired into Phase 1's existing
  EXTRACTING_CONCEPTS/BUILDING_GRAPH/GENERATING_STRUCTURE/VALIDATING_COURSE
  stages (already declared, unimplemented, in frozen-scope's own pipeline)
  rather than a separate "generate" endpoint. One cohesive call inside
  EXTRACTING_CONCEPTS; the other three stages succeed trivially immediately
  after — finer per-stage progress within curriculum generation is deferred.
- **API**: GET/PUT /courses/{id}/structure (the outline review gate —
  GET defaults to the latest generated version regardless of status, not
  only an active one), GET /courses/{id}/graph, POST .../publish-structure
  (the only route that ever changes `course.active_version_id`).

### Bugs found before they shipped

- Four service methods (`get_graph`, `rename_lesson`, `get_review_version`,
  `get_active_structure`) called `CourseService.get_owned()` directly, which
  raises `CourseNotFound` — but the router only ever caught
  `CurriculumNotFound`, so every one of those routes would 500 for another
  user's course instead of returning 404. Caught while writing the ownership
  tests, not by inspection. Fixed with one private `_get_owned_course()`
  translation point every method now goes through, rather than a
  try/except repeated at each call site.
- `get_graph()` defaulted to the *active* version, while `get_structure`
  correctly defaulted to the latest *reviewable* one — so a freshly
  generated, unpublished course showed an empty graph even with real
  concepts already persisted. Made consistent.
- Same class of test-fixture bug as Phase 1, three more times: SQLite's
  `Uuid` column type needs real `uuid.UUID` values, not the JSON strings an
  API response hands back. Each occurrence was a test writing its own
  assertion query, not application code.
- `FakeGenerationGateway.when_prompt_contains()` is a plain substring match;
  my first end-to-end test registered `"memory"` (lowercase) against text
  containing "Memory management" (capitalized) and silently matched nothing,
  producing zero concepts instead of the two the test meant to exercise.

### Live verification against real Gemini, not just fakes

`gemini-2.5-flash-lite` (AGENTS.md §5's frozen generation model) is also
retired for new callers, same class of failure as the earlier Groq and
embedding-model retirements. The API's own error names
`gemini-3.5-flash-lite` as the replacement; verified working directly, and
`GEMINI_GENERATION_MODEL` was already a setting, not a literal, so this was
a one-line default change — except `backend/.env` carried an explicit
override to the retired name from before the retirement was known, which
`docker compose restart` does not clear: **`restart` does not re-read
`env_file`; only container recreation does.** Lost real time to this before
finding it — worth remembering for any future env-file change.

With the corrected model, ran the real pipeline end to end on two small,
previously-unused fixture documents (virtual memory / paging; deadlock /
deadlock prevention) through the live Docker stack:

```
9 real concepts extracted, correctly deduplicated, zero false merges
8 real prerequisite edges proposed, sensible HARD/SOFT distinctions
  (e.g. "Four Conditions of Deadlock" -> "Deadlock Prevention" is HARD --
  correctly reflecting you need the conditions before prevention makes sense)
5 modules / 5 lessons, clustered by the source documents' own heading
  structure, in document order
Validation: READY, zero errors
```

Chased what looked like a real encoding bug (definitions containing mangled
em-dash bytes) through the database (`psql`: correct), the raw HTTP response
bytes on disk (hex-dumped: correct 3-byte UTF-8 `e2 80 94`), and the Gemini
SDK's own output (correct). The data is correct end to end; the mojibake was
introduced by my own diagnostic commands' terminal/pipe handling on Windows,
not the application. Recorded here specifically so a future session does
not re-open this as a real bug without re-checking the raw bytes first.

### Not done — deferred, not silently dropped

- PUT /graph (editing prerequisite edges) — GET only this phase.
- Dropping a concept or reordering a module via PUT /structure — lesson
  rename only.
- Finer per-stage job progress within curriculum generation (one call
  covers all four frozen stage names internally).
- Content generation (default lesson variant) and real assessment-question
  generation from blueprints — blueprints exist and are validated for
  coverage; no question text is generated yet.

Suite: 363 passing.
