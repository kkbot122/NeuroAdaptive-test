# Screen contracts

Proposed screens; route names and visual styling are not prescribed.

| Screen | Essential content | Actions |
| --- | --- | --- |
| Sign in | Product purpose; Google sign-in; recoverable auth error | Sign in |
| Dashboard | Course title/state/progress; first-course empty state | Create course; Finish setup / Review outline / Continue studying |
| Course setup | Name, goal, files, validation; optional subject and previous-course link | Add/remove/replace sources before publication; Prepare course |
| Processing | Current/completed stages; outline and first-activity readiness; clear failure reason | Back to dashboard; Review outline when ready; Retry / Replace source before publication |
| Outline review | Modules, lessons, objectives, prerequisite relationships; coverage warnings | Rename modules/lessons; Publish |
| Diagnostic introduction | Purpose, question count, submission rules, meaning of skipping | Take diagnostic; Skip for now |
| Course overview | Inspectable outline; concept labels/evidence strength; coverage; current activity and reason | Continue studying; inspect outline/progress; Back to dashboard |
| Activity workspace | Title, purpose/reason; activity-specific content; saved position | Format switch; tutor/source panels; Ready for questions or Start questions; Back to course |
| Concept remediation | One selected concept and evidence-based reason; source-linked explanation, worked example, recap, and question readiness | Study the focused concept; mark reading complete with Ready for questions; retry preparation |
| Targeted practice | One concept, factual evidence reason, and question count | Start fresh questions directly; no lesson or reading completion |
| Challenge | Selected taught concepts, factual reason, and question count; single-concept fallback is labeled plainly | Start fresh application questions directly; no lesson or reading completion |
| Assessment | One MCQ/short-answer question; count/progress; submission/pending states | Submit answer; Next question; Back to course |
| Results | Saved answers; correctness or unresolved grading; supported expected reasoning and sources; before/after concept labels and evidence strength; separate lesson coverage | Continue when grading is complete; Retry grading; Return to dashboard |
| Completion | Coverage and demonstrated understanding; limits of the estimate | Return to dashboard; Optional practice |
| Account settings | Tracking preference; independent presentation reset; account controls | Save; Reset preferences; Sign out; Delete account |

Reviewer screen (restricted): question, answer, rubric, original judgment, report,
correction history. Actions: retain judgment or record a correction with a reason.
Explicit reviewer authorization is required; this is not a general learner screen.

## Workspace

- Teaching remediation has a focused concept screen. Targeted practice and challenge
  have distinct question-first screens; they do not open a substituted lesson page.
- New lesson and interrupted-lesson study continue through the P2 lesson screen.
- Main content: objective, explanation, example, recap for teaching activities.
- Concise, detailed, worked example, analogy formats retain the same learning objective.
- Tutor receives course/activity context. Sources open supporting passages with document
  and page where available; TXT/Markdown use headings/locations without invented pages.
- Tutor/source panels preserve activity position. Responsive behavior must retain this
  context on smaller screens.
- No selector for alternative activities; outline inspection does not launch lessons.
- Load saved validated content when available. Reuse prepared formats; show preparation
  for uncached variants without losing position. Tutor shows progress while generating/
  checking; unvalidated answer text is not displayed.

## Assessment and results

- Tutor assistance disabled during assessments, available on results.
- Answers lock only after confirmed submission. Retry must not create a second attempt.
- Correctness/explanations withheld until the question set is submitted, including on
  resume. Grading-pending questions remain clearly pending.
- Reading finished, assessment submitted, grading finished, and understanding demonstrated
  are separate states, not one Complete Lesson button.
- Reports promise no immediate correction. Results distinguish original/corrected
  judgments; pending grading never appears as incorrect.
- Results refresh reads the saved session and evidence only. Continue resumes existing
  work or persists one next recommendation; unsupported preparation is named and
  recoverable. Supported P4 selections open their distinct activity screens. Dashboard
  is a direct exit.

## Shared states

Every data-dependent screen needs loading, empty, failure/retry, and unavailable
handling. Show actionable learner language, not raw provider errors. Preserve entered
work and saved progress. Foreign/deleted resources must not expose private content.
When generation pauses, saved content/progress remain accessible; identify unavailable
actions clearly. Settings distinguish required learning records from optional telemetry.

## Progress vocabulary

Not assessed; Needs attention; Developing; Proficient; Mastered. P3 uses the approved
uncalibrated `mastery-v1` label thresholds and a separate evidence-strength label. Explain
that labels summarize course evidence and do not prove real-world mastery or learning
gain. Do not show precise mastery percentages. Keep lesson coverage and concept
understanding separate.
