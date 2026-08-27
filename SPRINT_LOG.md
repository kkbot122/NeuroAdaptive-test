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
