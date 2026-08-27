# NeuroLearn — Sprint Log

Running record of the autonomous 2-day build. Newest entries at the bottom.
Each entry: what was built, what was decided and why, what was skipped, the
commands actually run, and what the next item needs.

---

## 2026-08-28 — STEP 0: reconciliation

### Planning docs

All four were present in the working tree but **untracked**, so nothing
inspecting the repository could see them. Now committed (`e1343f9`).

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
