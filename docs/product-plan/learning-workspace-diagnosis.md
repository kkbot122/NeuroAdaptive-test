# Learning workspace diagnosis — 2026-10-08

## Scope and conclusion

Inspected the current working tree, product plan, existing Docker logs, container model configuration, and saved local PostgreSQL records. This checkout has substantial uncommitted learning/backend/frontend changes; findings refer to those files, not just committed branch history.

The learning lifecycle has working pieces, but preparation and recovery can block the whole journey. A published outline does not mean teaching or lesson questions are ready. Failed preparation remains the active activity, so Continue returns the student to the blockage. Failed grading also prevents progression until resolved.

This diagnosis made no production code changes or new AI requests. Only the existing database container was started for read-only inspection; the web, API, Redis, and worker remained stopped. Historical records establish failures and some successful journeys. They do not establish current provider availability or successful browser operation on today's working tree.

## What the project does with AI

The table below records the original diagnosis. Dated implementation updates at the
end describe subsequent fixes, including exhaustive tutor grounding on 2026-10-09.

The ordinary provider constructors in [providers.py](../../backend/app/services/providers.py) create Gemini gateways. The existing API and worker containers specify `gemini-3.5-flash-lite` for generation and `gemini-embedding-001` for embeddings. Saved lesson/question/tutor records also name the generation model. These are observed configuration/provenance values, not an external verification of model availability.

Actual outbound provider calls are centralized in:

- [generation/gemini.py](../../backend/app/services/generation/gemini.py): `model.generate_content(...)`.
- [embedding/gemini.py](../../backend/app/services/embedding/gemini.py): `client.embed_content(...)`.

| Student action | Execution path | AI work |
| --- | --- | --- |
| Process uploaded sources | Processing API → Celery `neurolearn.processing.run` → jobs/curriculum services | Embeds extracted passages; proposes concepts; embeds concept definitions; performs concept normalization where needed; proposes prerequisite edges. Module/lesson grouping and assessment blueprints are built deterministically. Blueprints are not actual lesson questions. |
| Publish outline | Curriculum `publish-structure` → preparation dispatcher | Queues first-lesson teaching and assessment in `detailed` format after publication. |
| Continue studying | Frontend `activities/next` → learning selection → preparation request | Resumes saved work or selects through the rule/scoring engine. Selection itself does not call an LLM. Missing artifacts are queued for generation. |
| Open study / change format | Activity `content?format=...` → preparation service | Reuses matching validated content; otherwise schedules content preparation. Main preparation also prepares questions; alternate formats normally prepare content only. The frontend polls pending/running preparation. |
| Prepare lesson/remediation | Celery `neurolearn.preparation.run` → `_generate_content` | Generates typed objective/explanation/example/recap JSON, then makes separate AI support checks for factual statements. Saves only content passing the required checks and coverage gates. |
| Prepare questions | Same worker → `_generate_questions` / `_validate_question_set` | Generates a mixed question set, then checks premises, correct answers, explanations, distractors, short-answer answerability, and rubrics. Adaptive sets also check isolated concept attribution. |
| Ask tutor | Next.js proxy → FastAPI tutor service → retrieval/generation/validation | Embeds the question, searches owned course passages, generates a cited answer, semantically checks sampled claims, may regenerate/strip/abstain, and saves the turn. This runs in the API request, not Celery. |
| Start diagnostic | Learning diagnostic API → mastery diagnostic generator | Generates MCQs synchronously from sampled concept names and definitions; saves a fixed session. It does not use the lesson question preparation validator. |
| Save assessment answer | Learning service; Celery for short text | MCQs are graded by comparing saved answers. Short answers use AI rubric grading with up to three counted calls. Evidence/mastery updates and next-activity ranking are computed in code. |
| Inspect sources, saved results, or progress | Database-backed APIs | Normally no new AI generation. Some apparently read-only activity/content routes can dispatch preparation or grading recovery. |

The frontend reaches the API through [the authenticated Next.js proxy](../../frontend/app/api/v1/[...path]/route.ts). A browser network trace therefore shows local `/api/v1/...` requests; Gemini calls happen server-side.

There is also an older lesson-content endpoint in [tutor/router.py](../../backend/app/modules/tutor/router.py) that generates lessons through tutor retrieval. The current study workspace uses saved activity preparation instead. These are two different teaching paths with different validation/cache behavior.

## Confirmed saved failures

| Course / activity | Evidence | Effect on the journey |
| --- | --- | --- |
| Distributed Systems: Replication, Consensus & Recovery (`415aafb3-e7d2-4696-869a-f3840a28d7c4`) | Published; 3 documents; 16 extracted passages; 11 diagnostic MCQs; **zero teaching artifacts and zero lesson questions**. Selected new lesson is `RECOVERABLE_FAILURE`. Detailed preparation records 51 calls / 3 retries; concise variant records 110 calls / 5 retries; both paused with `AI_ALLOWANCE_EXHAUSTED`. Earlier first-lesson attempt records `CONTENT_SCHEMA_OR_GROUNDING_FAILED`. | Diagnostic results can exist while studying cannot begin. Continue resumes the failed lesson; changing format cannot guarantee available teaching. |
| Distributed Systems (`218d5473-d4c4-41b9-98d5-be124a27a13f`) | First lesson and submitted assessment completed. Its analogy variant failed three candidates with `CONTENT_SCHEMA_OR_GROUNDING_FAILED`. Next remediation has saved worked-example/detailed teaching, but its main preparation paused at `QUESTIONS` with `QUESTION_ATTRIBUTION_NOT_ISOLATED` after 37 recorded calls. | Reading remediation content can work while its assessment and the next learning cycle remain blocked. A ready content-only variant is not evidence of ready questions. |
| Relational Database Demo | Submitted targeted-practice session has a short answer in `GRADING_FAILED`, failure `grading_allowance_exhausted`. | Saved answers/results remain accessible; unresolved grading prevents the activity completing and the ordinary Continue progression. |
| Published course named `test` | No saved content or questions. First preparation paused with `AI_ALLOWANCE_EXHAUSTED` after 35 recorded calls. | Publication succeeded despite first-learning readiness failing. |

The short answer that logged two `GradingError` failures in Distributed Systems eventually graded successfully on its third counted call. It is **not currently stuck**. The Relational Database Demo answer is the observed unresolved grading failure.

Tutor records include both successful `source_only` answers and `insufficient` answers after retrieving six passages. Thus retrieval can succeed while answering/validation still fails. Those records alone cannot distinguish genuinely inadequate evidence from generation/parser/validation failure.

## Why the flow feels blocked

### 1. AI call amplification and allowance exhaustion

[Preparation validation](../../backend/app/modules/preparation/service.py) uses separate generation calls for each check. For a fully successful default five-question set:

- Four MCQs: six checks each (stem, answer, explanation, three distractors) = 24.
- One short answer: stem, answerability, expected reasoning, and two checks for each of three rubric criteria = 9.
- One question-set generation = 1.

That is **34 calls before teaching generation/validation**. Adaptive sets add one attribution check per question, bringing the question stage to 39 calls. Rejected candidates repeat work. One saved successful first-lesson preparation records 49 calls; successful targeted practice records 103 after retries.

The ordinary [daily allowance](../../backend/app/modules/abuse/service.py) is 200 calls per owner across courses. Today's database contains usage counters of 200 and 293. API/worker containers now have a developer exemption configured, which permits counting above 200 for designated accounts. That does not clear previously failed jobs, remove provider limits, or establish that every owner is exempt.

Usage accounting is inconsistent: preparation and short-answer grading reserve allowance per gateway call; tutor request controls reserve once around potentially several generation/validation calls. Gemini rate-limit retries occur inside the gateway and are not individually represented in the outer counter. Embedding usage is not a complete provider-cost ledger. The UI and current counters cannot explain exact provider requests, tokens, cost, or latency.

### 2. Content validation fails, but the detailed reason is lost

Saved failures and worker logs establish rejected content candidates. `CONTENT_SCHEMA_OR_GROUNDING_FAILED` combines parsing/schema errors and other exceptions; logs record that category without the rejected field or validation error. Saved drafts and per-check outcomes are unavailable in the inspected preparation records.

The current validator requires objective, explanation, example, recap, and concept coverage after unsupported factual statements are removed. The prompts also require examples/comparisons grounded in supplied passages. These constraints need inspection against an actual rejected response. **We cannot yet establish whether the observed failures come from malformed JSON, missing sections, citation mapping, overly restrictive checks, inadequate sources, or model output quality.** Increasing retries alone does not resolve that uncertainty.

### 3. An unfinished failed activity holds the course

[Learning selection](../../backend/app/modules/learning/service.py) resumes an unfinished activity before ranking new work. [Preparation acquisition](../../backend/app/modules/preparation/service.py) returns without executing a job already marked `RECOVERABLE_FAILURE`; explicit Retry is required. A reset allowance or configured exemption does not automatically restart it.

[The results screen](<../../frontend/app/(pages)/courses/[courseId]/assessment/page.tsx>) enables ordinary Continue only after grading completes. These gates preserve evidence integrity, but missing content/questions or unresolved grades can keep a student at the same activity indefinitely. The product plan still leaves some terminal recovery behavior open.

### 4. Recovery can target the wrong preparation

In [the study screen](<../../frontend/app/(pages)/courses/[courseId]/study/[lessonId]/page.tsx>), Retry passes the selected format. Backend [retry](../../backend/app/modules/preparation/service.py) chooses a variant whenever that format differs from the original assessment preparation. If variant content is already ready but the main question job failed, Retry can return “The requested format is already ready” instead of retrying questions.

In [the remediation/activity screen](<../../frontend/app/(pages)/courses/[courseId]/activities/[activityId]/page.tsx>), Retry omits the format entirely and therefore targets the main preparation even when a displayed alternate variant is what failed.

These are confirmed request/handler mismatches in the current code. Their exact browser symptoms were not reproduced during this inspection.

## Tutor and presentation gaps

- **Conversation context:** [TutorService.ask](../../backend/app/modules/tutor/service.py) uses the current question, retrieved passages, and a lesson-title hint. `conversation_id` groups stored messages but no earlier turns are loaded into the prompt. The panel clears displayed turns on mount/context reset and restores only the conversation ID. Follow-ups such as “explain that again” therefore lack conversational context.
- **Failure reporting:** provider/parsing failures can become the same “insufficient evidence” answer as missing source support. The user cannot tell which failed.
- **Grounding differs by path:** prepared teaching validates every factual statement; the tutor's [semantic checker](../../backend/app/modules/tutor/validation.py) samples every second claim. It also trusts the model's claim list to cover the prose. This falls short of the product plan's all-factual-claims requirement.
- **Tutor lock scope differs:** backend disables tutor help if the owner has any open assessment across courses; the integrated panel checks the current course. An assessment left open elsewhere can cause a seemingly available tutor to return HTTP 409. No open session was observed in the inspected local snapshot; this is a code-path discrepancy.
- **Streaming:** the API finishes generation and validation before emitting SSE. The panel also reads the stream before committing the answer. “Checking course sources…” can therefore last through multiple AI calls with no visible partial answer.
- **Formats:** [PreparedLessonContent](../../frontend/components/PreparedLessonContent.tsx) changes section order/style and reads format-specific generated statements. Diagram renders point cards without relationships/edges. Source view emphasizes citations but does not directly render original passages as the main content. Quiz first still shows teaching and requires the existing reading/question gate. Analogy prompts permit a supported example when no source analogy exists. The selector offers more distinct experiences than the implementation currently delivers.
- **Diagnostic:** its generation prompt uses concept definitions rather than cited original passages, and its parser does not apply the grounded lesson-question checks. Diagnostic success is not proof that the prepared teaching/assessment path works.

## Next diagnostic and repair order

1. Capture one failed content candidate's schema error or rejected support checks, with course/activity/preparation IDs, stage, model, durations, and counted requests. Keep credentials and private source text out of ordinary logs. Establish the exact failure before adjusting prompts or validation.
2. Reconcile validation workload, allowance reservation, and provider request accounting. Preserve source validation while avoiding repeated checks of identical evidence and making exhaustion understandable/recoverable.
3. Make Retry identify whether content, a particular variant, or the main question stage failed. Exercise both saved failure cases above without replacing activities or questions.
4. Verify one complete real-source cycle: Continue → ready teaching → format switch → tutor → fixed questions → grading/results → next remediation/practice. Current historical successes cover portions, not this full journey on the current checkout.
5. Address tutor conversation restoration/context and the difference between provider failure and inadequate evidence; then align exposed presentation formats with their actual behavior.

No new provider attempt or full browser cycle was run in this diagnosis. The precise content rejection cause and present-day hosted/provider behavior remain unverified. Linked-course grounding, full course-completion criteria, and other explicitly deferred milestones should not be counted as implemented merely because they appear in the plan.

## First repair slice — 2026-10-08

Implemented locally after the diagnosis:

- Retry from ready alternate teaching content now resumes a failed main preparation rather than returning “The requested format is already ready.” Main retry preserves saved content and resumes the failed stage.
- Remediation Retry identifies a failed displayed content variant. When alternate content is readable and questions failed, it retries the main job. Ready question IDs and content artifacts are preserved.
- Content schema rejection logs now include field locations and error types, with course/model metadata. Source-support rejection identifies model abstention, missing sections, or rejected statement positions when a section has no supported remainder. Logs omit generated field values, full exception text, and unknown field names.
- Regression coverage exercises the HTTP retry endpoint, preservation of ready questions/content, both rendered remediation Retry cases, and log privacy. Existing prompt-version, concise-prompt, and source-button test expectations were aligned with the working tree.

Verification: backend preparation tests **39 passed**; frontend tests **43 passed**; frontend typecheck and lint passed. Provider generation, stored failed jobs, and the full browser journey have not been rerun. This slice repairs recovery targeting and diagnostic visibility; it does not resolve allowance exhaustion, improve generation success, add tutor history, or implement new format experiences.

Next repair sequence:

1. Use the new failure information to reproduce one real-source rejected content draft and correct the exact schema/source-support mismatch.
2. Reduce repeated provider checks with bounded structured validation and reuse of identical checks, maintaining coverage of every required factual claim. Measure actual calls and elapsed time for teaching and a full mixed question set; reconcile the daily allowance and account for hidden gateway retries.
3. Recover saved failed preparation/grading through supported APIs and verify an actual course cycle through the next activity, retaining saved question IDs, answers, and evidence.
4. Add bounded, ownership-scoped tutor history and conversation restoration, distinguish provider failures from insufficient evidence, and reconcile tutor assessment locking and claim validation with the product contract.
5. Implement the agreed presentation experiences and verify format switching and assessment transitions with real prepared content.

## Controlled live preparation follow-up — 2026-10-08

Retried the same saved new-lesson activity in **Distributed Systems: Replication, Consensus & Recovery** through the supported preparation Retry API. Ran the real preparation task directly against the existing database and configured Gemini provider, with ordinary queue workers stopped so unrelated queued work could not execute. The diagnostic task process used one candidate per stage and disabled lookahead; these temporary bounds did not change deployment configuration or production defaults.

| Preparation | Result | Counted generation/validation calls | Task elapsed time |
| --- | --- | --- | --- |
| Saved failed concise content variant | READY; teaching artifact saved | 7 | 9.265 seconds |
| Saved failed main detailed teaching + assessment | READY; teaching and five questions saved | 49 | 207.456 seconds |

Both succeeded on their first candidate under the current prompt/schema/provider configuration. **No content rejection reproduced in these runs.** The older schema/grounding failures still cannot be explained precisely from their original coarse logs. No prompt or source-validation change was made in response to this successful retry.

Two individual validation gateway calls in the main run took **72.253** and **72.722 seconds**, accounting for about 145 of its 207 seconds. The configured gateway includes rate-limit retries, but this probe did not separately capture transport attempts or prove the specific upstream cause of each delay. The 49-call measure is gateway/accounting usage, not an exact network-request or token count.

API verification afterward established:

- The original activity ID is retained and its status is READY.
- Detailed and concise content return READY, with all required lesson sections.
- Main assessment readiness is true; its saved set contains **four MCQs and one short answer**.
- Repeated activity/content reads add **zero** counted preparation calls and preserve question IDs.
- No reading completion, student answers, mastery evidence, or replacement activities were created by this operation.

This recovers the selected lesson in this course; it does not establish success for every lesson/format or the full browser assessment/grading/next-activity journey. Existing developer allowance exemptions remain configured; ordinary account allowance behavior was not changed. The next measured problem to repair is validation call amplification and provider latency, followed by a real repeated learning cycle.

## Validation call reduction — 2026-10-08

Implemented source-isolated batching for preparation validation, with the unvalidated,
configurable `P2_VALIDATION_BATCH_SIZE_V1=12` bound. Checks sharing exactly the same
cited source text can use one request; unrelated passages remain in separate requests.
Every factual statement and required question/rubric judgment still receives an
explicit result. IDs and booleans are parsed strictly; incomplete responses fail
closed. Identical successful claim/source pairs are reused within the stage checker.
Source ownership/mapping checks, ordinary daily allowance, saved artifact reuse, and
fixed assessment membership remain authoritative.

Replayed the same real-course content and question drafts captured in the prior run
using the new production validators and real Gemini gateway. This preserved the saved
course and questions; only the actual benchmark calls incremented daily usage.

| Validation workload | Earlier individual checks | Final source-isolated batches |
| --- | --- | --- |
| Counted validation calls | 47 | 9 (4 teaching, 5 questions) |
| Sum of gateway call durations | 197.292 seconds | 8.965 seconds |
| Source support / question validation | Passed | Passed |

The final run logged nine successful application-level provider attempts and no
gateway rate-limit backoff. Gateway logs now expose attempt count, latency, and
backoff intervals without logging source text or provider error payloads. Provider
eligibility, token cost, and SDK-internal retries are not inferred from these counts.

Checks: **140 focused backend tests**, **2 PostgreSQL preparation/allowance concurrency
tests**, Ruff, and diff whitespace checks passed. Tests cover all-check inclusion,
source isolation, bounded batches, exact-pair reuse, missing/duplicate/unknown IDs,
nonboolean results, provider failures, supported distractor rejection, saved-artifact
preservation, tutor compatibility, and atomic budget accounting. The deterministic
preparation fixture dropped from 39 counted calls to at most 9.

The live result measures validation only; it does not establish a new full preparation
time or verify a complete browser learning cycle. The next acceptance step is studying
the recovered lesson, submitting its fixed assessment, checking grading/results, and
continuing to the next selected activity.

## Browser learning-cycle acceptance — 2026-10-08

Executed in native Chrome with the existing authenticated account, against an isolated
copy of the local database (`neurolearn_cycle_check_20261008`) and Redis databases
12/13. No synthetic progress was inserted: test answers were entered through the
normal browser screens, APIs, and Celery grading path. The original database was
checked separately and retained its previous reading, assessment, and activity state.

The browser account owned the earlier **Distributed Systems** course
(`218d5473-d4c4-41b9-98d5-be124a27a13f`), so acceptance used that course's copied
Retry Limits and Backoff remediation rather than the other account's recovered course.

### Blocker found and corrected

The main remediation questions again failed `QUESTION_ATTRIBUTION_NOT_ISOLATED`.
A captured candidate showed that source support, expected answer, explanation, and
distractor judgments passed, but the error-classification question failed attribution.
The validator received the label “Retry Limits and Backoff” without its curriculum
definition. That definition explicitly includes distinguishing temporary failures from
validation errors, which was the rejected question's subject.

One targeted real-provider check changed only that context: the label-only result
was false, and including the saved curriculum definition made it true. Adaptive
attribution checks now include both the label and definition while retaining all
source, isolation, answer/rubric, and freshness requirements. New question metadata
records `p5-question-rubric-grounding-v2`. Existing fixed question sets are retained.
A failing-then-passing regression covers the missing definition; the existing rejection
regression still verifies that non-isolatable questions are withheld.

### Acceptance observations

| Step | Observed result |
| --- | --- |
| Dashboard Continue | Resumed the saved remediation ID `c1b145d9-f14c-4cc9-bc44-4a7b4785d1b8` and exposed its failed questions job |
| Retry after correction | Real Celery delivery prepared four MCQs and one short answer on its first candidate, using six counted calls; the existing teaching artifact was reused |
| Study and sources | Detailed and worked-example variants loaded; View source opened the owned source passage beside the lesson |
| Tutor | “Why should retries use jitter?” returned a source-backed answer and citation |
| Ready for questions | Recorded reading completion and opened fixed session `48be58e5-829a-430e-95d8-e00a7418793b`; tutor help was unavailable during questions |
| Answer locking / interruption | Confirmation saved and locked answers; reload after question one resumed at question two, with feedback still withheld |
| Submission / pending grade | Results initially showed four graded MCQs and one unresolved short answer, explicitly not counted as incorrect; Continue waited for grading |
| Completed grading | Five of five graded, zero unresolved; short-answer feedback showed three of three rubric criteria met, source links, and binary evidence |
| Learning results | Concept changes showed Needs attention → Developing; lesson coverage remained separately displayed as one of three |
| Continue | Selected one new Failure Domains and Redundancy remediation, ID `2a71f541-2be2-44f3-8f96-8baa4f1f4594` |
| Next activity / reload | Same next activity resumed; teaching and five questions became ready, with Ready for questions enabled |

Database checks found **five answers, five question attempts, and five mastery evidence
events** for the accepted session. Grading recovery created no duplicate attempts or
evidence. The copied course had exactly one unfinished next activity. The original
course still had no reading completion or assessment session for the tested remediation,
and the acceptance-only next activity did not exist there.

### Remaining limits and checks

This is one complete local browser cycle using real saved sources and providers, not
hosted verification or a claim that every course/format succeeds. The corrected retry
took **186.155 seconds for six calls** in this run: individual provider requests were
roughly 28–35 seconds, despite no application-level rate-limit backoff. This contrasts
with the faster earlier replay and demonstrates upstream latency variability.

Short-answer grading required **three counted calls and 89.429 seconds**: two responses
failed the strict grading parser before the third produced a valid judgment. Recovery
worked, but malformed grading responses and provider latency remain operational issues.
The next remediation required two question candidates and 12 total preparation calls.

Verification after the scope fix: **141 focused backend tests**, **2 PostgreSQL
concurrency/allowance checks**, Ruff, and diff whitespace checks passed. Normal database
and queue configuration was restored after acceptance; test learning records remain
confined to the isolated copy.

## Short-answer response shape fix — 2026-10-08

Replayed the acceptance short answer without changing its saved answer or judgment.
The existing JSON-only grading request produced a valid object on one call, then a
JSON array on the next. Pydantic rejected the array with `model_type`. This reproduced
one concrete source of the grading retries: JSON mode constrained syntax, but the
request did not constrain the response's root object and criterion array.

Short-answer grading now sends a provider response schema requiring an object with
`criteria_met`, strict boolean items, and minimum/maximum array lengths equal to the
saved rubric's criterion count. The generic generation gateway accepts an optional
schema, and its Gemini adapter translates array-bound names for the installed 0.8.3
SDK. An offline SDK serialization regression covers that compatibility path. The
domain parser still rejects wrong roots, string/number booleans, extra fields, and
incorrect criterion counts; it does not coerce a malformed answer into a grade.

Grading error logs now identify schema locations/types or expected/received criterion
counts using answer/question IDs. They omit learner text, provider payloads, unknown
field names, and validation input/context. Malformed responses remain unresolved,
preserving the saved answer and creating no attempt, judgment, or mastery evidence.
Rubrics, binary evidence thresholds, immutable question sets, and the three-call
grading limit are unchanged.

Live post-fix checks against the same isolated answer:

- Three independent positive replays all returned a valid object on the first call,
  with all three criteria met. Durations: **15.779, 26.091, and 23.554 seconds**.
- An unrelated “I do not know” test answer returned a valid object with all three
  criteria unmet in **21.257 seconds**, confirming the schema does not force a pass.
- No replay overwrote an existing student answer or grading judgment. Actual probe
  usage was counted in the isolated database; provider quota remained real.

Verification: **161 focused backend tests** and **4 PostgreSQL lifecycle/concurrency
checks** passed, including malformed-response evidence withholding, logging privacy,
SDK schema serialization, grading retry limits, lease preservation, and duplicate
prevention. Ruff and diff whitespace checks passed. This addresses the reproduced
response-shape failure, not every possible provider error or a latency guarantee.

## Tutor continuity and recovery — 2026-10-08

Implemented locally using the existing `tutor_messages` records; no new conversation
table or database migration is required.

- History reads are scoped inside the query by owner, course, conversation UUID,
  lesson context, and saved decision. Pages default to 50 turns, with a scoped cursor
  for earlier messages and `Cache-Control: no-store`.
- Follow-up prompts use at most six recent turns and 12,000 characters of prior
  question/answer text. Retrieval receives at most 2,000 characters of the latest
  topic context, allowing pronoun-only follow-ups to retrieve relevant passages.
  These are named, configurable, unvalidated V1 bounds. Prompt version is now
  `tutor-prompt-v2`.
- Previous conversation is explicitly untrusted context, not factual evidence or
  instructions. New answers still use current owned-course retrieval and citation
  checks. This change does not expand retrieval to earlier linked courses or change
  the existing semantic sampling policy.
- Browser storage retains only the conversation grouping UUID. Existing session
  IDs are migrated to persistent local storage when present; successful turns and
  citations are restored from the server, including after closing/reopening a tab.
  Session-only IDs already lost before this change are not automatically recovered.
- Provider failures, malformed answers, and unavailable semantic checks now return
  typed HTTP 503 errors. They do not create misleading insufficient-evidence turns.
  Genuine missing source support remains a saved insufficient-evidence response.
  The entered question and earlier chat survive a failed request; incomplete SSE
  responses are reported as interrupted rather than unsupported material.
- The history response reports the same account-wide assessment restriction used
  by the backend POST guard. Open assessments withhold tutor history and disable
  submission, including from submitted-results views in another course. The guard
  is checked again before delivery if an assessment starts during generation.
- Integrated and standalone tutor views use the shared panel. Late responses from
  an old activity cannot replace current chat or source content. History refresh and
  loading earlier messages are read-only and make no provider calls.

Live native-Chrome acceptance used the existing isolated course copy. The first
question asked why two replicas in the same rack may not provide useful redundancy.
The follow-up “Can you explain that more simply?” stayed on that topic and returned
current course citations. Reload, then closing/reopening the tab, restored both turns
in their saved order. Exactly two new `source_only` turns were recorded with the V2
prompt. No restoration action generated another answer.

A saved assessment was then opened through the normal UI in the isolated course;
the standalone tutor for a different owned course displayed the assessment lock and
disabled its input. No answers were entered into that assessment. Original student
progress and tutor records remained outside this acceptance database.

Checks: **161 backend tests** and **48 frontend tests**, frontend typecheck/lint,
backend Ruff, deterministic OpenAPI/TypeScript contract checks, and whitespace checks
passed. Tests include owner/course/context isolation, pagination, bounded history,
untrusted-history channel separation, account-wide and mid-generation assessment
locking, failure distinctions, stale responses, and reload restoration. The existing
limiter reset fixture was moved into shared test setup because each fresh test database
reuses user IDs; production rate limits were unchanged.

Services were restored to their normal database/queue configuration and stopped after
acceptance. Upstream latency, full factual-claim semantic coverage, and the incomplete
diagram/source-view/quiz-first experiences remain separate work.

## Tutor grounding implementation — 2026-10-09

Closed the semantic sampling gap for new tutor replies and the independent-prose
escape path. The production tutor now:

- Checks every claim block, with explicit `sample_every=1` and source-isolated
  batching. Every factual assertion inside a block must be supported; partial
  support is not a passing judgment. Claims citing the same source text share a
  bounded request, without importing other claims' unrelated evidence.
- Generates ordered, self-contained Markdown answer blocks under a native response
  schema, with provenance `tutor-prompt-v3`. The schema also serializes successfully
  through the installed Gemini SDK in an offline check.
- Assembles both displayed and saved prose only from blocks whose ownership,
  retrieved-source, and semantic checks passed. It no longer relies on replacing
  rejected substrings within an independently generated answer. Unlisted prose,
  paraphrases, altered negations, and unsupported retry text cannot escape through
  that answer field. Blank claim text/citations are rejected.
- Checks that a trimmed remainder still answers the question. A supported definition
  cannot stand in for a rejected solution or procedure. This adds one native,
  strict-boolean request only when text has been removed; whitespace-only differences
  do not trigger it. A negative judgment abstains. Missing, malformed, or unavailable
  judgments return `VALIDATION_UNAVAILABLE` without persisting a partial answer.
- Uses fixed application text for insufficient evidence, including when the model
  sets that flag with unchecked prose. Research-only disabled-validation behavior
  remains explicit and is not accepted as an ordinary API request option.

Verification: **771 backend tests passed**, **19 infrastructure integration tests
skipped**, and **48 frontend tests passed**. Frontend typecheck, backend Ruff, and
whitespace checks passed. Frontend lint had no errors and two existing warnings
(an unused test callback parameter and a panel hook dependency). Tests include the
formerly unchecked second claim, source-isolated tutor batching, valid Markdown
preservation, incomplete batches, stripped-answer adequacy, strict verification
responses, HTTP/SSE delivery, saved history, retry handling, and fallback text. Scoped
standards and specification reviews found no remaining actionable findings after
the adequacy check was added.

No live provider calls, browser acceptance, or infrastructure integration run was
performed for this slice. Previously saved tutor messages retain their original
prose and validation provenance; they are not retroactively upgraded to V3. Semantic
and completeness judgments still rely on model checks, not a mathematical guarantee
of correctness. The legacy lesson-content endpoint inherits the same new policy.
Upstream latency, complete provider usage accounting, hosted acceptance, and the
unfinished diagram/source-view/quiz-first experiences remain separate work.

## Presentation formats implementation — 2026-10-09

Implemented the three incomplete presentation experiences in the shared lesson renderer,
used by both ordinary lesson study and focused remediation. Both screens offer the same
seven formats and keep the saved activity, reading position, and assessment handoff.

- **Diagram:** prepared explanation points now have a visual directed graph and an
  inspectable connection list with citations. Every connection has strict endpoint
  indexes, mapped source provenance, and a semantic check including both endpoint text
  and the relationship's direction. Layout ordering never invents a link. Invalid or
  unsupported connections reject preparation and expose another-format/retry recovery.
  Diagram-specific prompt/schema/validation versions prevent reusing old point-card
  artifacts as checked graphs. Old content can be upgraded while retaining fixed
  question IDs; other format caches remain reusable.
- **Source view:** original cited passages lead the view, with filenames, headings, and
  page metadata when available. Distinct IDs are fetched once through the existing
  owned-course endpoint, rendered as plain text, and linked to the Sources panel.
  Aborted or late replies cannot replace another course's view. A failed passage has
  its own retry while the saved source-grounded teaching remains readable.
- **Quiz-first:** each typed learning objective produces an ungraded warm-up question.
  An attempted answer or explicit uncertainty reveals the matching validated explanation,
  example, and recap for self-comparison. The learner completes these reveals before
  the ordinary reading-completion and ready-assessment handoff. Warm-ups make no new
  generation/grading calls, expose no fixed assessment answers, and create no mastery
  evidence. Answers and reveal state restore on the same browser, scoped by activity
  identity and artifact; this is not cross-device response storage.

Verification: the full offline backend suite passed **785 tests**, with **19
infrastructure-dependent checks skipped**. After the final diagram failure-message
change, **57 focused preparation/presentation tests** passed. Frontend checks passed
**56 tests**, including rendered graphs, original passage metadata/inert text, passage
retries, stale responses, warm-up restoration, and both lesson/remediation assessment
handoffs. TypeScript, backend Ruff, OpenAPI/TypeScript contract checks, and whitespace
checks passed. Frontend lint has no errors and the same two prior warnings. The
production frontend build passed using build-only placeholder credentials.

These checks use deterministic providers and rendered component tests. No new live AI
calls, native-browser acceptance, or deployed infrastructure run was performed for
this slice. Existing Docker services were inspected and left stopped. Remaining work
includes upstream latency, provider usage accounting, and broader hosted acceptance.

## Preparation/tutor latency and attempt accounting — 2026-10-09

Implemented focused changes after four deterministic reproductions showed hidden SDK
retry eligibility, one preparation counter increment for two transport attempts, one
tutor request reservation for answer plus validation, and serialized independent checks.

- Gemini generation and embedding disable SDK retries explicitly and use bounded
  request timeouts. The application default is one same-provider retry with a 2-second
  backoff, replacing three retries and 10/20/40-second sleeps. Preparation and grading
  stop the domain retry loop on provider/verification unavailability; malformed or
  unsupported candidates retain their existing quality retry rules.
- Independent source-validation batches overlap up to two requests in PostgreSQL
  deployments, with copied request context and separate accounting sessions. Every
  batch still sees only its cited source text. Native result schemas reduce malformed
  batch responses, while strict IDs/booleans remain authoritative. Scheduling is bounded
  and stops launching further batches after failure; at most already-running requests
  finish. Offline SQLite/injected-gateway paths remain serial.
- Interactive AI operations have a 120-second default deadline. Each SDK timeout is
  capped by its remaining operation time. PostgreSQL reservation locks/statements are
  bounded to 3 seconds; dispatch time is checked again after commit. Known unsent,
  deadline-cancelled reservations refund daily/resource counters and are recorded
  separately from actual attempts.
- `ai_provider_calls` durably itemizes the owner, feature, phase, resource, operation UUID,
  model, generation/embedding kind, retry index, input item count, status, capacity wait,
  provider elapsed time, and returned token metadata. No prompts, responses, source
  text, credentials, or guessed prices are stored. Quota, resource counter, and STARTED
  record commit together before dispatch. Preparation and fixed-answer grading count
  transport retries individually; processing stage counters derive from this ledger.
- The 200-attempt UTC generation allowance remains the generation admission policy.
  Embeddings are separately itemized, including retries and batch item counts. Missing
  token reports stay null, with explicit incomplete-usage totals. Historical quota
  reservations without ledger rows are identified rather than retroactively estimated.
  A process killed between reservation and result leaves an unfinished record; it is
  not presented as a confirmed completion or a provider billing reconciliation.
- `GET /api/v1/ai/usage` exposes only the authenticated owner's daily totals, feature
  totals, and a bounded recent-call list. Tutor completion now includes measured usage
  and its operation UUID for correlation. Account deletion removes the ledger.
- AI request burst limits, request concurrency, and outbound capacity use atomic Redis
  coordination across processes. Defaults allow four provider requests, at most two
  from workers, leaving two places for interactive work. Interactive capacity waits up
  to 2 seconds; background work can wait up to 60 seconds without reserving quota first.
  Expiring tokens recover interrupted holders. Redis admission fails closed; offline
  tests explicitly disable shared coordination. Other legacy/IP limits remain local.

Migration `9fd2c74a6e11` follows the existing P5 head `f5a1c9d2e7b4`. It was upgraded,
downgraded, and compared against current models in a separate test database, then applied
additively to the local application database. Schema comparison also caught an existing
question-uniqueness index absent from ORM metadata; its declaration now retains the
index already created by P2, without removing or weakening it.

Verification: **796 offline backend tests passed** (26 infrastructure checks skipped),
plus **7 focused real PostgreSQL/Redis tests**, and **56 frontend tests**. The integration
checks cover concurrent preparation counters, actual overlapping validation attempts,
cross-process capacity, worker capacity reserved for interactive requests, expired
holders, mixed lease durations, database contention before dispatch, and processing
embedding retries. Focused tests also cover grading call caps, cancelled dispatch refunds,
owner isolation, unknown token reports, and stopping queued validation on outage.
Ruff, frontend typecheck/lint, and generated OpenAPI/TypeScript contract checks passed;
the two earlier frontend lint warnings remain. These are deterministic SDK-boundary
checks with real database/Redis coordination; no new live Gemini calls were made, so
upstream wall-clock improvement is not yet measured. Provider latency can still vary.

The modern Gemini learning path is covered. Retired Groq chat/adaptation paths keep
their previous behavior and are outside this ledger. Rebuild the API and worker images
to run the updated code. Broader hosted acceptance remains separate work.
