# Learning rules

Agreed product and safeguard behavior. Remaining open numeric policies are listed in
[README.md](README.md); P3 progress-display defaults are recorded below.

P1–P4 implemented behavior is in the corresponding lifecycle, preparation, results,
and [P4 contract](p4-adaptive-activities-contract.md). The
rules below identify implemented behavior and pieces that await later work.

## Activities

| Activity | Eligibility/purpose | Work | Finish condition |
| --- | --- | --- | --- |
| New lesson | New material; no demonstrated prerequisite gap requiring attention | Objective, explanation, example, recap; assess only taught concepts | Teaching covered and assessment graded; weakness can remain |
| Remediation | The selected concept itself is Needs attention with More supporting evidence | Focused explanation, worked example, recap, and five fresh questions; teaching required before assessment | Follow-up graded; selection uses new evidence |
| Targeted practice | Previously taught concept with Not assessed, Limited evidence, or Developing evidence | Five fresh questions with no mandatory teaching or reading gate | Question set graded; weak evidence can lead to remediation |
| Challenge | Completed teaching and Proficient or Mastered evidence | Five fresh questions applying two eligible concepts when possible; otherwise a clearly labeled single-concept application | Question set graded; each question updates only its attributed concept |

Resume preserves an existing activity, not a separate pedagogical activity. Failed
generation/grading is not completion. An assessment can finish without mastery.

## Selection

1. Resume unfinished work, including its existing assessment and question versions.
2. Distinguish unknown knowledge from demonstrated weakness. Optional checks can clarify
   unassessed prerequisites; skipping must not turn unknown into incorrect evidence.
3. Prioritize demonstrated gaps affecting upcoming learning, then select suitable practice,
   new material, or challenges from available supported activities.
4. Use deterministic ranking within these eligibility/priority rules; record the selected
   activity, inputs, policy version, and factual reason.
5. Students cannot select alternatives through the outline. They can stop, resume, change
   presentation, and skip the initial diagnostic/optional prerequisite check.

Selecting remediation requires the selected concept's own assessment evidence. Unknown,
limited, or mixed/developing evidence routes to practice; absent source coverage never
implies weakness. Missing teaching support remains a saved, recoverable preparation
limitation. The general exit policy for an assessment that stays unavailable remains open.

## Assessment and evidence

- P2 implements lesson MCQs through the existing P1 fixed-session lifecycle. Its
  approved, unvalidated policy is 5 questions by default, increasing to cover each
  taught concept, capped at 8 (`P2_ASSESSMENT_*_V1`). Lessons exceeding the bound
  remain recoverably unavailable.
- P4 remediation, targeted practice, and challenge each use five fresh MCQs by default
  (`P4_REMEDIATION_QUESTION_COUNT_V1`, `P4_TARGETED_PRACTICE_QUESTION_COUNT_V1`,
  `P4_CHALLENGE_QUESTION_COUNT_V1`); these are named, versioned, configurable, and
  unvalidated defaults. The P2 hard cap remains eight.
- Persist a fixed set per assessment; one confirmed answer per question. No hints or
  same-question retries. Transport/grading retries reuse the saved answer.
- P2 MCQ correctness is binary with difficulty 0.5 (`P2_MCQ_DEFAULT_DIFFICULTY_V1`);
  both are unvalidated defaults. Mastery formulas and recommendation scoring are
  unchanged.
- Questions, expected answers, explanations, and feedback need supporting course
  passages. P2 rejects normalized duplicate prompts within the course version and
  never assigns diagnostic questions to a lesson assessment.
- Correctness and explanations appear after all questions are submitted. MCQ comparison
  is server-side; short answer uses an automated rubric judgment that can be wrong.
- Grading failure stores Awaiting grading, not incorrect. Evidence enters mastery once
  per successfully graded attempt; retries must not duplicate it.
- Results explain rubric points, concept changes, source support, and the next step.
- P3 results expose saved answers, grade or unresolved status, P2-supported expected
  answers/explanations, and source links only after the complete set is submitted.
  Graded and unresolved counts are explicit; pending answers never count as incorrect.
- Reporting preserves original evidence; no automatic score change or immediate-resolution
  promise. An authorized reviewer can correct the judgment. Keep the original judgment
  and correction history; recompute affected mastery/attributed outcomes without another
  attempt. Preserve historical decision traces. Access and correction propagation need contracts.

## Mastery and presentation

- Mastery comes from graded concept evidence, never reading, confidence, format switches,
  or learner success buttons. No evidence means Not assessed.
- Multi-concept questions need explicit concept attribution. Wrong challenge answers
  do not erase prior progress or automatically mark every related concept weak. A P4
  challenge question has exactly one attributed taught concept; the full set covers each
  selected concept, and validation rejects an item that cannot isolate its attribution.
- P3 labels use the existing `mastery-v1` classifier: no evidence → Not assessed; below
  0.40 → Needs attention; 0.40–<0.70 → Developing; 0.70–<0.85 → Proficient; Mastered
  requires mastery ≥0.85 and uncertainty ≤0.35. These are uncalibrated evidence labels,
  not claims of real-world mastery or learning gain; raw percentages are not shown.
- Evidence strength is separate: no evidence → Not assessed; recency-adjusted effective
  evidence weight below the `evidence-strength-v1` boundary (1.0) → Limited evidence;
  at or above it → More supporting evidence. This fixed, uncalibrated presentation
  boundary does not alter the mastery formula or adaptation inputs. A policy change
  requires a new evidence-strength policy version.
- Submitted assessment comparisons use the persisted submission timestamp for both
  states. “Before” excludes this session; “after” adds only its successfully graded
  evidence. Later grading retries update the same comparison, while later unrelated
  evidence and passage of time do not change it. The comparison is rebuilt from existing
  immutable evidence and session links; there is no second mastery store.
- Presentation preference is separate from mastery. Manual switches are weak preference
  signals; subsequent relevant assessment results inform effectiveness.
- Format changes preserve activity progress. No fixed learning-style identity drives
  the new learning path. Exact attribution and exploration policy remain open.

## Continue and milestone support

- Continue resumes unfinished activity/session work first. After completion, it persists
  one selection through the existing recommendation path; refreshing submitted results
  does not select an activity or request preparation.
- P4 routes selected remediation to focused teaching with a reading gate, while targeted
  practice and challenge prepare questions directly without a lesson or reading gate.
  Resume preserves the saved type, targets, decision, reason, version, format, question
  versions/order, and confirmed answers through the existing P1–P3 lifecycle.
- Lesson coverage remains a reading-completion measure and is presented separately from
  concept evidence.

## Sources and prerequisites

- Published sources stay fixed; new material creates a new course. Before publication,
  replacement keeps valid files, invalidates dependent passages/concepts/content/questions,
  and rebuilds affected artifacts. Replaced or invalid artifacts cannot be published.
- Source absence and knowledge absence are different. Missing explanations warn without
  blocking publication; detection must have support rather than invent a prerequisite.
- Subject grouping is optional. Explicit links permit earlier owned courses; grouping
  alone grants no reuse. Retrieval/evidence queries enforce links and ownership.
  Matching and attribution details remain open.
- Match concepts by meaning/scope, not names alone. Do not copy/merge mastery blindly;
  preserve origins and avoid double-counting. Uncertain matches offer optional checks.
- Unsupported teaching/questions fail honestly. Repeated remediation changes supported
  explanation/questions; never fabricate missing material. Preparation keys include
  purpose and target concepts, so unlike activities cannot share incompatible artifacts.

## Preparation and grounding

- Celery prepares artifacts asynchronously. PostgreSQL tracks stages/artifacts; Railway
  Redis handles delivery/coordination, not authoritative learning records.
- Prioritize waiting-student work, first-course readiness, then bounded lookahead.
  Reserve capacity so speculative generation cannot crowd out interactive work.
- P2 prepares the selected first lesson and assessment after the validated outline is
  published; the student can inspect/publish the outline while preparation runs.
  Studying starts when the first activity is ready, without waiting for all
  lessons/formats. The default format comes first; other formats prepare on demand.
- Cache validated artifacts/variants with provenance; reuse only matching course/source
  versions, concepts, and formats. Resume uses fixed sets; follow-up needs fresh questions.
- Use current-course or explicitly linked owner-authorized passages only. Uploaded/
  retrieved text stays data, never instructions or a tool invocation channel.
- Check every factual claim's citation ownership/existence and semantic support before
  display. Strip unsupported claims; abstain if the remainder cannot adequately answer.
  Structural checks alone are insufficient; all displayed factual text must be covered.
- P2 permits three candidates per stage (`P2_PREPARATION_MAX_CANDIDATES_V1`), then
  exposes a retry from the failed stage. One content-only next lesson may be prepared
  at lower priority after first readiness (`P2_PREPARATION_MAX_LOOKAHEAD_V1`). These
  are unvalidated bounds.
- Bounded retries stay on the configured provider, then pause with recovery. P2 counts
  each worker generation/validation request and candidate retry against the existing
  daily call-count budget. This is request accounting, not token accounting; deployed
  cross-worker capacity and actual provider usage remain to be verified. Outages or
  exhausted allowances preserve saved content/progress; ungraded answers stay pending.
- Keep required learning evidence/progress/decisions when optional telemetry is disabled.
  Reading time and interaction analytics are optional, not mastery evidence.

## Completion

Coverage records finished lesson work; understanding records concept evidence. Once
coverage and sufficient-understanding criteria are met, show completion and offer
optional app-selected practice. Remaining gaps offer targeted work. No schedule,
reminders, spaced review, programming execution, or OCR is added by this plan.
