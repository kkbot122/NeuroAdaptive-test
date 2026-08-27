# NeuroLearn — Implementation Status

**Phase 0 audit. Documentation only.**
Audited: 2026-08-26 / 2026-08-27. Verified by direct file inspection against the
working tree.

---

## 0. Legend, and a conflict about it

This document uses the five-value legend the Phase 0 prompt specifies:

| Status | Meaning |
|---|---|
| **Implemented** | Works and is production-shaped. |
| **Prototype** | Works, but is not production-shaped (no tests, no hardening, happy-path only). |
| **Partial** | Some of it works; named gaps remain. |
| **Planned-but-absent** | Designed somewhere; no code exists. |
| **Broken** | Code exists and does not work as written. |

> **Conflict flagged, not resolved.** Three different legends are now in play:
> the Phase 0 prompt's five values (above); the Phase 0 file's claim that
> `SYSTEM_ARCHITECTURE.md` §3 defines *Implemented / Prototype / Planned /
> Deferred*; and what §3 actually contains as authored on 2026-08-26 —
> *BUILT / PARTIAL / BROKEN / PLANNED / UNVALIDATED*. I used the prompt's legend
> here because it was the explicit instruction. **These should be reconciled to
> one vocabulary before Phase 1**, or every later status claim becomes
> ambiguous. My recommendation: adopt the five values above everywhere and add
> `UNVALIDATED` as an orthogonal *tag*, not a status — a thing can be both
> Implemented and UNVALIDATED, which is exactly the FSLSM engine's situation.

---

## 1. Current architecture

A Next.js chat application with a learning-style profile attached, talking to a
FastAPI backend over a shared-secret header. That is the whole system today.

```
Browser ──► Next.js (App Router, server components + route handlers)
              │  holds INTERNAL_API_KEY; validates NextAuth session
              ▼
            FastAPI /api/v1  ──►  PostgreSQL 16
              │                    (7 tables, 5-revision Alembic chain)
              └──► Groq (OpenAI-compatible SDK, llama-3.3-70b-versatile)

        ✗ no object storage   ✗ no vector index   ✗ no queue/worker
        ✗ no course/document/concept/mastery/assessment domain
```

Three client components bypass this path entirely and call the backend directly
from the browser — see §5, item 12.

---

## 2. The fifteen audit items

| # | Item | Status | Evidence |
|---|---|---|---|
| 1 | Frontend framework & structure | **Prototype** | Next.js 16.1.6 / React 19.2.3 / App Router / Tailwind 4. Routes in `frontend/app/(pages)/`: `chat`, `dashboard`, `mission`, `profile`, `quiz`, `read/[articleId]`, `signin`. Auth wiring in `frontend/auth.ts`. **No state-management library at all** — no zustand/redux/jotai/swr/@tanstack in `package.json`; state is server components plus local `useState`. Workable now, will not survive the course/lesson UI in Phase 9. |
| 2 | Backend structure: registered vs. on-disk | **Partial** | Four routers registered — `main.py:45-48`: `auth`, `content`, `profile`, `chat`. A fifth module, `app/modules/profiling/`, defines a full router with its own `prefix="/api/v1/profile"` (`profiling/router.py:9`) that is **never included** in `main.py`. Its models *are* imported (`main.py:12`), so the module half-exists: schema yes, endpoints no. |
| 3 | Schema evolution | **Broken** (as a discipline) | A healthy linear 5-revision Alembic chain exists, single-headed: `ea9facb393a3 → 2f4c2d25f29c → 6963bcb15db5 → 86bda7902ec8 → a1b2c3d4e5f6`. But `Base.metadata.create_all(bind=engine)` runs on every startup at `main.py:26`, which means the migration chain is not the mechanism that actually shapes the database. Both paths are live and can silently diverge. |
| 4 | Authentication | **Partial** | NextAuth 5 (beta) + Google. On sign-in, `auth.ts:24` posts to `/api/v1/auth/sync`. **The backend issues no token of its own and has no refresh path** — `python-jose`, `passlib`, and `argon2-cffi` are declared but unused for request auth. Authorization is entirely `x-user-email` + one shared static `x-internal-token`. See §3 risk R-1 and R-2. |
| 5 | File upload / storage | **Partial** | Upload exists only inside chat: `chat/router.py:110` accepts an `UploadFile`, parses it inline in the request, and **discards the bytes**. Nothing is persisted. No object storage of any kind. |
| 6 | PDF / document processing | **Prototype** | `pypdf` used synchronously inside the request at `chat/router.py:176-185`. No size limit, no page limit, no timeout, no type validation beyond a filename suffix and content-type check, no virus/format guard. A large PDF blocks a worker thread. |
| 7 | Vector database / search | **Planned-but-absent** | `qdrant-client==1.16.2` is declared in `requirements.txt` and imported **nowhere**. Confirmed dead weight, as are `boto3` and `minio`. Zero embeddings are computed anywhere in the codebase. |
| 8 | LLM integration | **Prototype** | Groq via the OpenAI-compatible SDK, instantiated **twice and inconsistently**: `chat/router.py:23` reads `settings.GROQ_API_KEY`; `services/adaptation.py:16` reads `os.getenv("GROQ_API_KEY")` directly, bypassing config. `google-generativeai==0.8.3` is declared but unused. Model id `llama-3.3-70b-versatile` is hardcoded at two call sites. No provider abstraction, no prompt versioning, no token/latency accounting. |
| 9 | Chat / tutor | **Prototype** | `POST /api/v1/chat/message` — the most complete feature in the repo. Sessions, ownership-scoped history (last 12 messages), surrogate sanitisation, FSLSM-or-archetype system prompt, keyword signal inference writing back to `raw_scores`. **It is not a tutor**: no retrieval, no grounding, no citations. It answers from model weights alone. |
| 10 | Course / module / lesson models | **Planned-but-absent** | Confirmed: none exist. Tables are `users`, `user_profiles`, `articles`, `paragraphs`, `article_readings`, `chat_sessions`, `chat_messages`. No `Course`, `Module`, `Lesson`, `SourceDocument`, `Concept`, `ConceptMastery`, `Assessment`, `AdaptationDecision`, or `AdaptationOutcome`. |
| 11 | Assessment / quiz | **Broken** (as a data path) | Three disconnected fragments. (a) A 60-line prompt protocol, `QUIZ_INSTRUCTIONS` at `adaptation.py:366`, asking the LLM to emit `<quiz>` JSON inline in a chat reply. (b) `app/(pages)/quiz/page.tsx` (228 lines) — **makes zero network calls.** It reads `current_quiz` from `sessionStorage:28` and writes `last_quiz_results` back to `sessionStorage:79`. (c) A `QuizResultsData` schema with hardcoded `q1_correct`/`q3_correct` fields at `profiling/schemas.py:13`, belonging to the **unregistered** router. **Consequence: quiz results never reach the server at all — they are discarded when the tab closes.** No questions persisted, no attempts recorded, no server-side grading, nothing links a question to a concept. |
| 12 | Analytics / event logging | **Broken** | `POST /events/batch` does not exist. What exists instead: `TrackedParagraph.tsx:20`, `TrackedImage.tsx:21`, and `TrackedCodeBlock.tsx:20` each POST to `/api/v1/profile/pulse` — **a route that does not exist in the backend.** Every telemetry pulse this app has ever sent has 404'd. Detail below. |
| 13 | Background jobs | **Planned-but-absent** | No Redis, no Celery, no RQ, no ARQ, not even FastAPI `BackgroundTasks`. Zero matches across the backend. All work is synchronous in-request. |
| 14 | Tests | **Planned-but-absent** | No runner in either manifest — no pytest, no jest/vitest, no `test` script. `backend/test_db.py` is a connectivity script, not a test. **I did not run a test suite because there is none to run.** Coverage is 0% by construction, not by measurement. |
| 15 | Deployment config | **Partial** | `docker-compose.yml` defines `db`, `backend`, `frontend`. Target topology needs at minimum `redis`, object storage, a vector service, and a `worker`. Backend runs via `wait-for-db.sh`; both app containers bind-mount source for hot reload — a dev topology with no prod counterpart. |

### Item 12 in detail — the pulse endpoint

This is the clearest single defect found in the audit, and it is worse than a
missing route. From `frontend/components/TrackedParagraph.tsx:20`:

```ts
await fetch("http://localhost:8000/api/v1/profile/pulse", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ paragraph_id, seconds: 5, dimension: "textual" }),
});
console.log(`✅ Pulse saved for Paragraph ${paragraphId}`);
```

Five distinct problems in nine lines:

1. **The route does not exist.** Registered profile routes are `/me`,
   `/calibrate`, `/override`, `/update`, `/fslsm`, `/fslsm/nudge`,
   `/fslsm/signals`, `/fslsm/reset`. No `/pulse`.
2. **It logs success on failure.** `fetch` rejects only on network error, never
   on HTTP status, so `✅ Pulse saved` prints on every 404. This is why the
   defect survived: the console said it was working.
3. **Hardcoded `http://localhost:8000`** — breaks under Docker (`backend:8000`)
   and anywhere but one developer's laptop.
4. **Called from a client component**, so it is a browser→backend call that
   bypasses the Next.js BFF entirely.
5. **No auth headers at all** — no `x-user-email`, no `x-internal-token`.

Consequence for Phase 1: implementing `/pulse` naively would create an
**unauthenticated, browser-reachable write endpoint** — the exact shape of
`SYSTEM_ARCHITECTURE.md` invariant §18.3. The telemetry path must be rebuilt
through a server route handler, not merely have its missing endpoint added.

---

## 3. Components by status

**Implemented** — nothing yet meets this bar. No component has tests.

**Prototype** — Google OAuth → user sync; chat sessions with ownership-scoped
history; FSLSM continuous vector engine (`services/fslsm.py`, the cleanest module
in the repo — but UNVALIDATED, its deltas are hand-chosen); the
directive-selection adaptation engine (`services/adaptation.py`).

**Partial** — backend router registration; authentication; file upload;
deployment config.

**Broken** — telemetry/pulse (§2 item 12); the quiz data path (§2 item 11);
schema-evolution discipline (§2 item 3); the three-way archetype split (§5c).

> **Pattern worth naming.** Items 11 and 12 are the same failure twice: a feature
> that *appears* to work in the browser while producing nothing server-side. The
> pulse path logs `✅ Pulse saved` on a 404; the quiz path renders results and
> scores them client-side, then drops them into `sessionStorage`. Both are
> exactly the "claims that can't be checked against anything" problem the paper's
> §II-E identifies as the base system's core defect — reproduced at the code
> level rather than the prose level. **Every assessment signal Phase 3's mastery
> estimator needs is currently being generated and then thrown away.**

**Planned-but-absent** — vector search, background jobs, tests, and the entire
target domain: courses, documents, chunks, concepts, prerequisite graphs,
mastery, assessments, adaptation decisions/outcomes, citations, RAG.

---

## 4. Proposed NeuroLearn mapping

| Current | Becomes | Notes |
|---|---|---|
| `User`, `UserProfile` | `User` + `UserPreference` + `PresentationAffinity` | `UserProfile.raw_scores` and the FSLSM columns become affinity signals only — never mastery inputs. |
| `Article`, `Paragraph` | `SourceDocument` → `DocumentSection` → `SourceChunk` | Current model has no chunking, ordering metadata, or provenance. Extend, don't reuse in place. |
| `ArticleReading` | `LearningSession` + `LearningEvent` | The pulse telemetry rebuilt properly lands here. |
| `ChatSession`, `ChatMessage` | `Conversation`, `TutorMessage` | Add course/concept scoping and a `Citation` relation. |
| `services/fslsm.py` | Presentation-affinity engine | **Keep.** Retarget from "learning style" to per-variant affinity. Best-factored code in the repo. |
| `services/adaptation.py` directives | Presentation-variant selection | Keep the dot-product mechanism; sever it from archetype labels. |
| `core/archetypes.py` | Onboarding flavor, or delete | See §5c. |
| `modules/profiling/` | Delete | Dead, unregistered, third vocabulary. |
| `QUIZ_INSTRUCTIONS` | `Assessment` + `Question` + `QuestionConcept` | Inline `<quiz>` JSON in a chat reply is not an assessment system. |
| — | `ConceptMastery`, `AdaptationDecision`, `AdaptationOutcome` | No current analogue. The core of the research contribution. |

---

## 5. The specifically-flagged items

> **Note on the prompt:** it says "verify or refute these **five** items" but
> lists four labels — `a`, `b`, `c`, `c` (the letter `c` is used twice, for two
> unrelated checks). I have treated them as four items and relabelled the second
> `c` as `d`. Flagging rather than silently renumbering.

### (a) Client-visible internal-token fallback — **PARTIALLY REFUTED**

The risk as stated ("exposed through a client-visible env var") is **not
occurring**. `NEXT_PUBLIC_INTERNAL_API_KEY` is defined in `frontend/.env.local`
but referenced by **zero** code paths, so Next.js never inlines it into the
browser bundle. All 8 real call sites use server-only `INTERNAL_API_KEY` inside
server components or route handlers. The variable should be deleted, but it is a
latent hazard, not an active leak.

**A different, real fallback problem was found instead.** Both sides ship the
same hardcoded default:

- `backend/app/core/config.py:12` — `INTERNAL_API_KEY: str = "dev_secret_key_123"`
- `frontend/auth.ts:29` — `process.env.INTERNAL_API_KEY || "dev_secret_key_123"`
- `frontend/app/actions/profile.ts:10` — same literal again

If the env var is unset on both sides, authentication **succeeds** against a
value committed to git. This fails open. Also `config.py:15` —
`SECRET_KEY: str = "CHANGE_ME_TO_A_RANDOM_SECRET_KEY"`.

### (b) Authorization defaulting to a fixed user id — **CONFIRMED, and since fixed**

`GET /api/v1/content/articles/{id}` was declared
`async def get_article(article_id: int, user_id: int = 1, ...)` with **no auth
dependency** — an unauthenticated endpoint taking a caller-supplied user id
defaulting to `1`. Any anonymous caller could write `ArticleReading` rows for any
user and read any user's archetype. Because the frontend never sent `user_id`,
**every article read in the system was logged against user 1**.

The same handler also passed a `str` where the adaptation engine required a
`dict` (`normalize_profile` calls `raw.get(...)`), making the endpoint a
guaranteed `AttributeError` on any article with at least one paragraph.

**This was repaired on 2026-08-26 under explicit user approval, outside Phase 0's
no-code-changes rule.** See §8.

### (c) The archetype implementations — **CONFIRMED, and there are three, not two**

| File | Vocabulary | Wired in? |
|---|---|---|
| `app/core/archetypes.py` | `THE_PIONEER`, `THE_VISUAL_ARCHITECT`, `THE_DEEP_SCHOLAR`, `THE_STRATEGIC_SKIMMER`, `THE_LOGICAL_TINKERER`, `THE_ADAPTIVE_GENERALIST` | **No — zero importers.** Dead code. |
| `app/modules/profile/service.py:6-11` | `THE_VISUALIZER`, `THE_ARCHITECT`, `THE_SPRINTER`, `THE_DEBUGGER` | **Yes** — the only one reaching the database. |
| `app/modules/profiling/router.py:60-67` | `THE_VISUALIZER`, `THE_SKIMMER`, `THE_SCHOLAR`, `THE_SYNTHESIZER` | **No — router never registered.** `THE_SKIMMER`/`THE_SYNTHESIZER` exist nowhere else. |

**The pack's description of this is out of date.** It says `adaptation.py`
"implements a different four-way rule-based text transform". It no longer does.
`adaptation.py` has already been rewritten into a continuous dot-product
directive engine whose own docstring reads *"No archetypes. No labels. No
hardcoded if/else trees."* The four labels survive only in a back-compat lookup,
`archetype_to_scores()` at `adaptation.py:404`.

Neither dead module is wired into anything that will become the real
mastery/adaptation engine. **Phase 1's headline task is largely already done, and
was done in the direction the paper wants.**

**Recommendations (not acted on):**

- `core/archetypes.py` — **delete.** Zero importers; its six system-prompt strings
  are superseded by the directive library. Keeping it invites a future
  contributor to wire it back in.
- `modules/profiling/` — **delete the router and schemas; keep `models.py`.**
  `UserProfile` lives there and is load-bearing. The router is a dead third
  vocabulary.
- `profile/service.py` archetype assignment — **keep as onboarding flavor only,
  clearly labelled.** It gives the calibration UI something to show. It must
  never be read by mastery or adaptation logic.
- `archetype_to_scores()` — **keep temporarily** as the migration bridge for
  existing rows, and delete once profiles are backfilled with real `raw_scores`.

### (d) `systemdesign.md` vs. System Design Document v1 — **CANNOT BE PERFORMED**

Reported explicitly rather than omitted, per the phase's own instruction:

**Neither document is available to me.**

- `systemdesign.md` (described as 3,544 lines, root, FR-AUTH-*/FR-ONB-*
  numbering) **does not exist in this repository** — not in the working tree, not
  on any branch, and never in git history. The only markdown present is
  `README.md`, `frontend/README.md`, and the three governance files authored
  2026-08-26.
- **System Design Document v1** (`neurolearn-system-design__1_.docx`, FR-1…FR-14 /
  NFR-1…NFR-12) was never provided to this session.

I therefore cannot list disagreements, and I will not manufacture them. **Zero
disagreements found is not the finding — "the comparison was impossible" is.**

Every `FR-*`, `NFR-*`, and "Deep Dive" reference in the governance documents I
authored traces to the phase pack's prose summary alone. The one concrete piece
of transcribed math — `mastery = (S + m0·k) / (W + k)`, `m0=0.3`, `k=2`,
`uncertainty = 1/√(1+W)` in `SYSTEM_ARCHITECTURE.md` §10 — **must be checked
against Deep Dive 4 before Phase 3 builds on it.**

---

## 6. Technical risks, ranked by how much they block later phases

| # | Risk | Blocks | Why |
|---|---|---|---|
| **R-1** | No test infrastructure | **All phases** | Phases 3–5 implement math whose correctness cannot be eyeballed. Building mastery estimation with no unit tests means never knowing if it works. Highest-leverage single fix in the repo. |
| **R-2** | Fail-open shared-secret auth with a git-committed default | Phase 1, 6 | One static token is the whole authorization model. Every ownership guarantee in Phases 2–7 rests on it. |
| **R-3** | No queue/worker; no object storage; no vector index | Phase 1, 2, 5 | Ingestion is definitionally async. Chunking + embedding a PDF in-request will time out. Phase 2 cannot start until this exists. |
| **R-4** | `create_all` racing the Alembic chain | Phase 1+ | Every phase adds tables. Two competing schema mechanisms means unreproducible databases. Cheap to fix now, expensive after 20 tables. |
| **R-5** | No provider abstraction; model id and client construction duplicated and inconsistent | Phase 5, 7, 8 | Phase 7 requires per-call provenance; Phase 8 requires swapping models to compare. Neither is possible with `AsyncOpenAI` built inline at two sites reading config two different ways. |
| **R-6** | Both learner-evidence paths discard their data (§2 items 11, 12) | **Phase 3, 4, 7, 8** | Telemetry 404s while logging success; quiz results are scored client-side and left in `sessionStorage`. Mastery estimation needs assessment evidence and adaptation-outcome analysis needs an event stream — **neither signal currently survives the browser.** Higher priority than first assessed: this blocks Phase 3, not just Phase 7. |
| **R-7** | Three archetype vocabularies | Phase 1, 4 | Mostly dead, cheap to clear. Left in place, someone re-wires a fixed label into the adaptation engine and violates invariant §18.7. |
| **R-8** | Integer PKs; target model specifies UUIDs | Phase 1, 2 | Decide before creating ~30 tables, not after. |
| **R-9** | No state-management library | Phase 9 | Survivable now; the course/lesson/mastery UI will not be pleasant without one. |
| **R-10** | `requirements.txt` is UTF-16LE with mixed line endings | Low | `pip` copes; most tooling does not. One-line fix. |

---

## 7. Recommended implementation order

**The pack's Phase 1–10 order is sound and I recommend keeping it,** with one
scope change and one sequencing note.

**Scope change to Phase 1.** Its stated headline — "resolve two divergent
adaptation implementations" — is ~70% already done (§5c). Repoint the freed
effort at R-1 (test infrastructure), which the pack currently defers to Phase 8's
evaluation harness. Unit-testing the mastery formula is not evaluation
infrastructure; it is the precondition for Phase 3 being trustworthy at all.

**Sequencing note.** R-8 (UUID vs. integer PKs) is a one-way door that must be
decided in Phase 1, before Phase 2 creates the bulk of the schema.

**Phase 1, in priority order.** Items 2, 3, 4 and 8 were completed on
2026-08-27 on branch `chore/stage1-foundation`; see `SYSTEM_ARCHITECTURE.md` §4
"Closed". The rest are open.

1. Install pytest + httpx; write the first unit tests (`fslsm.py`,
   `adaptation.py` math) and the first API test including a negative-authorization
   case. *(R-1)*
2. Delete both fail-open `"dev_secret_key_123"` defaults and the `SECRET_KEY`
   placeholder; fail loudly on startup if unset. Delete
   `NEXT_PUBLIC_INTERNAL_API_KEY`. *(R-2, and (a))*
3. Remove `create_all` from `main.py:26`; make Alembic the only schema path. *(R-4)*
4. Delete `core/archetypes.py` and `modules/profiling/{router,schemas}.py`;
   keep `profiling/models.py`. *(R-7)*
5. Decide integer vs. UUID PKs for new tables and record it in
   `SYSTEM_ARCHITECTURE.md` §8. *(R-8)*
6. Add `redis`, `minio`, and a vector service to `docker-compose.yml`; add a
   worker process. *(R-3)*
7. Introduce a single LLM provider abstraction; move both client constructions
   behind it. *(R-5)*
8. Convert `requirements.txt` to UTF-8. *(R-10)*
9. Rebuild **both** learner-evidence paths through authenticated Next.js route
   handlers, then implement their backend endpoints: telemetry pulses, and quiz
   submission (currently client-side-only in `sessionStorage`). Persist attempts
   server-side so Phase 3 has evidence to estimate mastery from. *(R-6)*

Then Phase 2 (ingestion → concepts → course), as the pack has it.

---

## 8. Scope conflict — declared, not resolved

Phase 0's no-side-effects check requires that `git status` show changes only
under `docs/`. **That check will fail**, and the failure is expected:

Earlier in this session, before the Phase 0 prompt was supplied, I presented the
audit findings and asked how to proceed. The user explicitly approved two actions:

1. **Hotfix defects (a)/(b)** — changed `backend/app/core/security.py`,
   `backend/app/modules/content/router.py`, and
   `frontend/app/(pages)/read/[articleId]/page.tsx`.
2. **Draft the governance documents** — created `AGENTS.md`,
   `SYSTEM_ARCHITECTURE.md`, `CONTRIBUTING.md` at the repository root.

Per `AGENTS.md` §1 and the Master Prompt, I am stating this conflict rather than
resolving it. Two defensible readings:

- **Treat the approved work as pre-Phase-0.** Phase 0 then produces only this
  document, and the no-side-effects check applies from here forward. *(My
  recommendation — the changes were authorised with full knowledge of the audit,
  and the security defect was live.)*
- **Treat it as a scope violation and revert.** Available, but it restores a
  live unauthenticated endpoint that also 500s on every call.

Nothing is committed. All changes remain in the working tree.

---

## 9. Verification honesty

| Check | Ran? | Result |
|---|---|---|
| `python -m py_compile` on changed backend files | **Yes** | Passed |
| `npx tsc --noEmit` | **Yes** | No errors in changed files; 7 pre-existing errors in gitignored `.next/dev/` build artifacts |
| Circular-import check, new `security.py` → `auth.models` | **Yes** | Clean |
| Backend test suite | **No** | None exists |
| Frontend test suite | **No** | None exists |
| Application runtime / `docker compose up` | **No** | Backend dependencies not installed locally |
| The repaired `/content/articles/{id}` endpoint | **No** | Verified by inspection and syntax check only — **not executed** |

Every status in §2 is traceable to a named file, and to a line number where one
applies. No claim in this document rests on inference from naming alone.
