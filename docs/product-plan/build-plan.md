# Build plan

Ordered work packages, not authorization to start code changes. Reuse existing modules.
Resolve dependencies first. Agreed architecture/safeguards are in [README.md](README.md);
numeric and operational details remain open.

## Before implementation

- Agree ownership/operating instructions and resolve the package's remaining policy
  details. Carry approved scope revisions into its contracts explicitly.
- Inspect affected code/tests. Establish explicit activity, assessment, answer, grading,
  and completion contracts rather than treating a recommendation as progress.
- Every schema change needs a new Alembic migration. Update OpenAPI first and regenerate
  frontend types; avoid parallel handwritten response contracts.
- No new infrastructure/dependencies, migration deletion, data deletion, or history rewrite
  is implied by these tasks.

## Implementation order

| ID | Work package | Depends on | Demonstrated pass condition |
| --- | --- | --- | --- |
| P0 | Hosted foundation and worker/storage verification | Deployment/configuration | **Repository configuration implemented; hosted pass conditions remain unverified.** Vercel reaches Railway API; worker consumes Railway Redis tasks; API/worker access private Supabase originals; deployed identity/DB/migrations work; restart recovery demonstrated |
| P1 | Activity/assessment lifecycle and saved progress contracts | Policy review | **Implemented locally.** Durable resume, fixed question sets, safe answer/grading states, and coverage/mastery separation; see [P1 contract](p1-lifecycle-contract.md) for checks and limits. Not deployed. |
| P2 | Async first-activity preparation, grounded MCQ assessment, reliable submission | P1; question/citation policy | Unseen upload prioritizes validated lesson/question set; saved artifacts reused; submissions lock once; restart resumes same questions; feedback withheld until set submitted |
| P3 | Results, evidence, progress, and next-activity integration | P0, P2; mastery policy | Deployed graded answers update relevant concepts once; reading does not; results show changes; Continue uses recorded selection/progress; bounded next-activity preparation starts |
| P4 | Remediation, targeted practice, and challenge experiences | P3; eligibility policy | Weak/mixed/strong evidence reaches the appropriate distinct activity; fresh questions; no unsupported prerequisite teaching |
| P5 | Short-answer grading and authorized review | P2–P3; rubric/access/retention policy | Rubric feedback has sources; failed grading remains pending; retries don't duplicate evidence; reviewer correction preserves original judgment and updates evidence once |
| P6 | Course overview and workspace integration | P3–P5 | Outline inspection cannot launch alternatives; tutor/sources open alongside teaching/results; tutor unavailable during assessment; format and position preserved |
| P7 | Setup/review/recovery and account UX | P1; replacement/retention contracts | Renames persist; diagnostic skip/retry; replacement rebuilds dependencies; minimal tracking preserves learning; deletion covers new records/files |
| P8 | Subjects and explicitly linked earlier courses | P3; cross-course/matching policy | Unit 2 can use supported Unit 1 evidence/source links; uncertain match offers optional check; standalone path works; foreign courses inaccessible |
| P9 | Completion, full polish, and acceptance evidence | P4–P8; completion policy | Coverage differs from mastery; sufficient evidence leads to summary and optional guided practice; no unfinished advertised action |

Build MCQ end-to-end first, then add short answers and the full agreed scope. P1 is a
repository checkpoint; later checkpoints remain outstanding. Finalize layout/styling
across screens after the state contracts are stable; include responsive, empty, loading,
and recovery states.

## First milestone: P0–P3 together

A deployed, resumable lesson → assessment → results → mastery → next-activity flow.
Real providers/preparation for demonstrations; deterministic stubs in automated checks.
MCQ first, then short answers and all activity types. Preserve work across navigation/
worker restarts; check all factual teaching claims; retain saved-content access on failures.

Use bounded worker tasks with durable artifacts. Waiting-student work outranks initial
readiness, which outranks speculative work. Define dispatch recovery, leases,
acknowledgments, timeouts, retries, and cross-worker provider capacity; the queue alone
is not a recovery guarantee. Load validated saved content/variants without model calls.

## Verification per package

| Layer | Highest useful seam |
| --- | --- |
| Unit | Selection/eligibility, mastery, evidence attribution, matching decisions |
| Contract | Provider schemas; assessment/results/progress APIs; generated frontend types |
| Integration | Owned REST flows; migrations; durable resume; duplicate submission/grading; linked retrieval; reviewer correction/access; private storage |
| Pipeline | Deterministic provider fixtures; stage retries and invalid artifacts |
| Browser | Upload/review/diagnostic/study/assessment/results/resume/complete and failure recovery |

Run focused relevant checks, affected contracts/integration, format/lint/types, and build
where practical. Use provider adapters/stubs in automated tests; never spend quota
accidentally. Keep checks distinguishable as executed, inspection-only, or blocked.

## Final acceptance

- Two unseen native-text document sets complete repeated teaching → assessment → mastery
  → recommendation cycles through production paths, with recorded provenance/decisions.
- Demonstrate diagnostic skip, interruption/resume, weak-answer remediation, practice,
  challenge, pending grading, unsupported content, and optional completion practice.
- Demonstrate Unit 1 → Unit 2 linking separately, plus the standalone course path.
- Verify ownership and approved safeguards, including all factual teaching claims,
  linked-source boundaries, reviewer corrections, source replacement, minimal telemetry,
  and exhausted-allowance behavior.
- Verify deployment/recovery with Railway Redis and private Supabase Storage. Measure
  saved-content, tutor, grading, and preparation responsiveness separately.
- Demo a real prepared course and a separate fresh upload; no fake artifacts.
- No fake runtime output, mastery, or traces; no learner-content/answer/secret leakage
  in logs or commits.
- Report actual commands/results, limitations, migrations, and unresolved decisions.
  Automated checks establish specified behavior, not learning gain.

If learner evaluation is pursued, define usability and educational measures separately.
Do not reuse the PDF's historical counts as current acceptance evidence.
