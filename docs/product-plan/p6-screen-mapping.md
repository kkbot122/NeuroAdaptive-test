# P6 screen and reference mapping

Status: implemented locally from P5 integration commit `3d799b0` on
`feat/p6-frontend-workspace`. The HTML files in `neuroLearn-UI/` establish the
visual language; product-plan files and P1–P5 contracts remain authoritative for
behavior and displayed data. `neurolearn-assessment copy.html` is the assessment
and results reference. The older `neurolearn-assessment.html` is superseded.

## Shared visual and interaction inventory

The mockups use an editorial neo-brutalist system: near-black ink, light gray
canvas, violet primary actions, yellow selected states, mint success, coral
attention, square two-pixel borders, offset shadows, and Space Grotesk / DM Sans
type. The dashboard has a fixed-width left sidebar on desktop and a horizontal
navigation row at 860px and below. Other screens use their own top-bar and
content layouts. Focus rings are visible. Motion is brief and removed for
`prefers-reduced-motion`. Workspace/results columns collapse to one column at
tablet widths (about 860–980px); smaller activity cards stack at 640px.

The HTML uses tabs, expandable outline rows, format selectors, answer confirmation,
progress indicators, retry/report states, and diagnostic/result previews. Preview
controls, embedded sample course data, canned tutor text, and timeout-driven state
changes are reference-only; production screens must use React state and real API
responses.

| Reference | Route | Existing API/data seam | P6 behavior and differences |
| --- | --- | --- | --- |
| `neurolearn-landing-v3.html` | `/` | Auth session | Keep the product story and Google sign-in entry. Remove unsupported attention-tracking or learning-gain claims and dead demo controls. |
| `neurolearn-signin.html` | `/signin` | NextAuth Google provider | Keep recoverable auth errors and real provider action; no simulated sign-in state. |
| `neurolearn-dashboard (1).html` | `/dashboard` | `GET /courses` | Render actual courses and their lifecycle states; empty/loading/failure states use the same visual system. No sample progress. |
| `neurolearn-course-setup.html` | `/courses/new` | Existing create/upload endpoints | Preserve supported name/goal/source upload. Subject/linking controls are P8; replacement/recovery additions are P7. |
| `neurolearn-processing-outline.html` | `/courses/{courseId}/workspace` | Course, documents, latest job and its saved stages, structure/graph, retry, publish | Successful create-and-upload hands off here and starts the existing processing job once. Refresh resumes the latest job. Stage rows and outline are API-backed; lesson rename is supported; module rename and source replacement are P7. |
| Diagnostic intro (from `neurolearn-diagnostic-test.html`) | `/courses/{courseId}/diagnostic` | Read-only course/session context; `POST /diagnostic` only after Take diagnostic | Explain the fixed baseline set and saved-answer rules. Take opens the existing resumable assessment; continue to overview is a safe no-diagnostic path. No result preview. |
| `neurolearn-course-overview.html` | `/courses/{courseId}/learn` | `GET course`, `GET structure`, `GET graph`, `GET learning-state`; explicit Continue uses `POST activities/next` | Outline and concept tabs are read-only. The current route calls `POST activities/next` on page load; P6 changes this so browsing cannot persist a decision or trigger preparation. Continue resumes/selects once. |
| `neurolearn-workspace.html` | `/courses/{courseId}/study/{lessonId}` | Activity, saved content/format, progress patch, reading complete, tutor SSE, source chunk detail | Show only validated saved content. Persist format and reading position; source clicks open real chunk metadata. Replace mock chat with validated tutor output and one saved client conversation identity. |
| `neurolearn-concept-remediation.html` | `/courses/{courseId}/activities/{activityId}` | Activity, graph, content/preparation, retry, progress patch, reading complete, assessment | Preserve activity identity/version/format. Saved explanation remains visible through question-stage failures; remediation requires the reading-complete API before its fixed set starts. |
| `neurolearn-targeted-practice.html` | `/courses/{courseId}/activities/{activityId}` | Activity/preparation, retry, assessment | Question-first activity; no teaching or reading gate. Count and target come from the saved activity. |
| `neurolearn-challenge.html` | `/courses/{courseId}/activities/{activityId}` | Activity/preparation, retry, assessment | Question-first application. Explain the single-concept fallback only when the saved target has one concept. |
| `neurolearn-assessment copy.html` | `/courses/{courseId}/assessment` | Fixed session, answer, submit, retry grading, report issue | Authoritative assessment/results mock. Preserve fixed questions/order, persist-before-lock, no feedback before submission, grading states, rubric vs binary evidence, reports/corrections, coverage vs understanding, real Continue and dashboard exit. Tutor is unavailable during questions and available after submission. |
| `neurolearn-diagnostic-test.html` | `/courses/{courseId}/assessment?type=diagnostic` | Existing diagnostic session endpoints | Same assessment shell with MCQ-only diagnostic questions and no mastery feedback until submission. Preview tabs and sample course claims are removed. |
| `neurolearn-reviewer.html` | `/grading-reviews` | P5 allowlisted review queue/detail/actions | Keep the P5 authorization boundary and real report, fixed question/rubric, cited source, and append-only review data. No preview persona or fixture rows. |
| `neurolearn-diagnostic-completion-settings.html` | Existing diagnostic/results/dashboard surfaces where supported | P1–P5 session/progress APIs | Reuse visual treatment only. Course completion policy, settings, telemetry controls, and other later actions are P7/P9; no nonfunctional controls or premature completion claim. |

## Main implementation gaps at P5

- `/learn` selects an activity while rendering, so outline browsing currently has
  a durable side effect. P6 must split read-only overview from the explicit
  Continue action.
- The lesson and adaptive screens are separate, narrow activity pages; tutor and
  source panels are standalone or absent. P6 integrates source/tutor beside
  teaching and results while preserving question lockout.
- Lesson and adaptive polling need single-flight requests, stale-response guards,
  terminal-state stop conditions, and unmount cleanup. No artificial delays are
  permitted.
- Current lesson persistence and saved variants must remain tied to the P1 activity
  ID and course version; legacy lesson entry must resolve through that lifecycle.
- P5 already provides generated contracts for mixed questions, grading recovery,
  reports, and reviewer correction. The frontend should consume those contracts
  without creating local copies of response types.

## Deferred scope

P7 owns source replacement/dependent rebuilds, setup recovery additions, broader
account settings, and tracking preferences. P8 owns subjects and earlier-course
linking. P9 owns a completion policy and optional post-completion practice. The
diagnostic and completion/settings reference file contains later-phase controls;
only existing lifecycle actions are exposed in P6.

## P6 implementation

| Screen/journey | Route and main implementation |
| --- | --- |
| Landing and sign-in | `/`, `/signin`; shared tokens, responsive cards/hero, real Google provider action, recoverable sign-in errors |
| Dashboard and course setup | `/dashboard`, `/courses/new`; real owned course states, published-course coverage, empty/error/loading states, supported title/goal/confidence inputs |
| Processing and outline review | `/courses/{id}/workspace`; create/upload hands off with a one-time processing flag. The route checks the latest job before starting, polls saved stages, supports retry, and renders real outline objectives/concepts, rename, and publish actions in the mock's sidebar/two-column composition. |
| Diagnostic introduction | `/courses/{id}/diagnostic`; existing saved activity is respected; diagnostic creation occurs only after an explicit action |
| Course overview | `/courses/{id}/learn`; `CourseOverview` uses read-only course/outline/graph/learning-state GETs. Only Continue calls `POST activities/next`; dashboard navigation exits directly. |
| Lesson and adaptive activity | `/courses/{id}/study/{lessonId}`, `/courses/{id}/activities/{activityId}`; saved activity/version/session/format, validated content, serialized progress writes, remediation reading gate, question-first practice/challenge, retry and separate preparation status |
| Assessment and results | `/courses/{id}/assessment`; fixed sessions and answer writes, confirmed locking, mixed question summary, grading retry/report, rubric and binary evidence, correction history, coverage, contextual results tutor, explicit Continue and dashboard exit |
| Tutor and sources | `LearningSidePanel` in lesson, remediation, practice, challenge, and submitted results; same authenticated tutor/source endpoints, session-scoped conversation identity, safe Markdown, page or heading metadata |
| Authorized review | `/grading-reviews`; shared visual system around the P5 queue/detail/decision APIs; backend role authorization is retained |

Shared tokens and focused classes live in `frontend/app/globals.css`. The
dashboard follows the supplied sidebar, featured continuation card, and stacked
course rows. Its Continue button is the explicit existing lifecycle selection
action; page load remains read-only. Google Fonts are loaded with the same
Space Grotesk / DM Sans stylesheet link used by the HTML references. The shared
interactive support panel is `frontend/components/LearningSidePanel.tsx`.
Route-specific components are `CourseOverview.tsx` and `DiagnosticIntro.tsx`.
The older standalone tutor route checks the current saved assessment and remains
unavailable until submission; results open tutor clarification beside the saved
questions and citations.

Two optional fields were added to `AssessmentSessionOut`: `decision_id` and
`lesson_id`. The existing `activity_id` alone was insufficient to retain tutor
context on submitted results without an activity GET that can reconcile or queue
preparation. The result response also now includes the immutable original rubric
score, feedback, and binary judgment when a reviewer correction exists. Both
changes are response-only: no schema migration and no grading, mastery, or
correction-policy change. `backend/openapi.json` and
`frontend/lib/generated/api.ts` were regenerated and checked.

The course overview's `GET /learning-state` can reconcile the durable status of
an existing activity, as defined by P1. It does not select another activity or
request preparation. No overview/result refresh invokes the next-activity POST.

The landing and course setup surfaces were refined against their HTML references
after the initial P6 browser pass. `/` now uses the `neurolearn-landing-v3.html`
navigation, hero/illustration frame, action buttons, three-step cards, feature
grid, five-label evidence legend, FAQ, violet call to action, and footer. Copy in
the hero and evidence panels remains accurate to current behavior; the example
understanding labels in the mock are not presented as a real learner state.
`/courses/new` uses `neurolearn-course-setup.html`'s desktop sidebar, centered
form/review columns, divided form sections, square fields, dashed source area,
and violet offset-shadow review card. It sends selected files through the
existing authenticated upload flow and routes to the course workspace for
preparation. The reference's example files and unreadable-source state remain
illustrative; the screen only shows files the learner actually selects and the
real upload result. The unsupported subject/link control remains deferred to P8.

## P6 limits

- The setup and processing screens expose only actions supported by current
  endpoints. Source replacement/rebuild recovery, subject and linked-course
  controls, broad settings/tracking controls, and final completion policy remain
  deferred as listed above.
- Activity screens can show fixed question count before an assessment opens;
  exact MCQ/short-answer counts are displayed from the immutable session once
  available. The server's preparation response does not expose question types
  before session creation.
- Frontend route/state regressions use the Node test runner. Targeted rendered
  interaction regressions use React Testing Library with jsdom; they cover
  stale content/citation removal, tutor lockout and post-submit clarification,
  retry errors, and one-time reading restoration. These are component
  interaction tests, not full Chrome end-to-end coverage.
- The local account used for browser inspection is not in the P5 reviewer
  allowlist. The reviewer route now waits for the API decision and shows a
  restricted-access screen without queue controls when the API returns 404.
- The reference fonts load from Google Fonts in the shared root layout. Browsers
  without network access fall back to the declared system font stack.

## Verification record

P5 baseline before P6 edits: `npm test` passed (7 tests), `npm run lint` passed,
and `npm run typecheck` passed. After P6 implementation, all 31 frontend Node
tests and five rendered interaction tests pass; frontend lint and typecheck
pass; the optimized production build passes; OpenAPI export and generated
TypeScript contract checks pass; Ruff on the changed backend files passes; and
`git diff --check` passes. The functional-review follow-up adds five rendered
frontend interactions and three tutor API cases; all pass with the targeted
suite. The first sandboxed production build attempt was blocked when Turbopack
tried to bind a temporary CSS worker port; rerunning the same build with the
approved local process permission succeeded.

Browser inspection used the existing local Compose app and its real saved
courses, activity, source, and submitted-session data. After the dashboard
visual correction, the authenticated `/dashboard` was compared directly in
Chrome with `neurolearn-dashboard (1).html`: Space Grotesk / DM Sans, the
240px sidebar, selected Courses item, featured continuation card, and bordered
course rows now follow the reference. Runtime course names, statuses, and
progress stay API-backed. The course overview was also checked against its HTML
reference. The landing hero, sign-in panel, saved remediation, source passage,
processing-failure recovery, submitted diagnostic results, and reviewer-denied
screen were also inspected. At a 393 px iPhone 16 viewport, the course overview,
diagnostic introduction, and submitted results were inspected; cards stack into
one column without horizontal overflow. The overview displayed a saved remediation without extra selection;
the diagnostic intro respected that unfinished activity; its citation opened the
real `neurolearn-relational-databases.txt` passage with `Page 1`; results showed
the real eight-question MCQ session and its saved evidence.

In the current styling follow-up, the live unauthenticated `/` and authenticated
`/courses/new` were compared in Chrome with `neurolearn-landing-v3.html` and
`neurolearn-course-setup.html`. The live landing retained the same navigation,
two-column hero, illustration frame, heavy square borders, offset violet shadow,
and section rhythm. Course setup retained the 240px sidebar and the same
back/title/form/review hierarchy, divided fields, dashed upload zone, and violet
shadow checklist. The app used Chrome's current 90% site zoom while the
reference tab used 100%, so the comparison was made by layout and proportion
rather than raw pixel size. No screenshot file was saved. The two screens'
narrow layouts were not directly captured in this follow-up; their
single-column/horizontal-navigation breakpoints remain in place.

The diagnostic introduction was compared side-by-side in Chrome with the
Diagnostic state in `neurolearn-diagnostic-completion-settings.html`. Its centered
card, header hierarchy, yellow label, bordered information rows, violet offset
shadow, and action area follow the mock. The HTML's fixed “12 questions,” “about
10 minutes,” and mixed-question claims are omitted because the current diagnostic
contract provides an MCQ-only fixed session whose count is session-specific. The
real course had an unfinished activity, so the app showed its saved-activity
warning and prevented starting a second activity. This follow-up was checked at
desktop width; the narrow-width layout was not recaptured.

One retry of the failed lesson preparation was exercised in the local app. The
live content endpoint reported pending/running while the workspace still showed
generic “not ready” copy; a progress indicator for this state was added and
interaction-tested. The attempt then returned to recoverable failure after three
candidate attempts with `INSUFFICIENT_SOURCE_SUPPORT` as the saved error category.
The database stores only the final candidate category, so this does not establish
that all three failed for the same reason. No content artifact was saved; no further
provider retries were made. The P2 trace records the correction and why post-fix live
verification remains blocked.

Browser inspection did not submit or start a new assessment, send a live tutor
prompt, retry the failed source job more than once, or create fixture progress. Consequently,
the in-progress assessment form, allowlisted reviewer queue, a ready-but-
unpublished outline, normal lesson, and question-first activity were not visually
inspected with live data. Their route/state integration is covered by the local
frontend regressions and backend tests. No screenshot file was saved; the visual
comparison above records the checked screens and the source/result state used.

### Processing and outline visual follow-up

The processing and outline screen now follows `neurolearn-processing-outline.html`.
The create-course upload flow hands off to `/courses/{id}/workspace?startProcessing=1`;
the page removes that one-shot flag, checks the latest job first, and only starts
processing when uploaded documents exist and no job is saved. Subsequent visits
resume the job instead of creating another one. Stage labels and statuses come
from `JobOut.stages`, and outline review uses the saved `StructureOut` plus graph
concept names. The mock's staged first-lesson and first-question generation is
not part of this course-processing job: those activities are prepared through
the approved P2 learning flow when study begins. The page says this accurately
instead of claiming those assets are ready. Source replacement remains deferred
to P7; preview controls, sample outline content, and sample coverage warnings
are omitted.

### P6 functional review fixes

- Prepared lesson content, preparation state, and saved reading position are
  keyed to the current course and activity identity. A response is accepted
  only when that identity and the selected format are still current, and
  citations are derived only from visible, matching content. Changing the
  activity also resets the tutor/source conversation panel context.
- Reading position restores once after the selected activity's content loads.
  A format switch saves the current scroll position and cannot replay the
  activity's initially loaded position.
- Grading retry failures render beside the retry control, and submitted-results
  action errors render independently of whether all grading has finished.
- Both tutor chat and the legacy lesson-content endpoint return HTTP 409 while
  the owner has any open assessment session. The integrated panel checks the
  saved active session and disables tutor submission; the server check remains
  authoritative for stale tabs and direct API requests. Submitted assessments
  continue to allow results clarification.
- Regression coverage includes rendered tutor locking and post-submission
  clarification, reading restoration across a format change and activity
  switch, retry failure visibility while grading is incomplete, stale activity
  content/citation removal, response staleness, and both guarded tutor
  endpoints. No database schema or generated API data field changed; the new
  HTTP 409 response is documented in OpenAPI.

### Live course preparation follow-up

The local uploaded-source course processed successfully and produced a published
outline, but the first lesson had no saved content artifact: its preparation
exhausted three candidates at source/grounding validation. The saved source files,
16 extracted chunks, and outline were intact. Lesson prompts now request a compact
set of source-checkable statements using a versioned cache key; validation remains
strict and unsupported drafts are still withheld. The study workspace reports this
failure category in plain language and retains its retry action. A live retry with
the compact prompt also exhausted three candidates as `INSUFFICIENT_SOURCE_SUPPORT`;
no lesson artifact was saved. The workspace now exposes real pending/running
preparation progress rather than looking idle while content is being checked. More
provider retries were stopped after this result to avoid unnecessary quota use.
