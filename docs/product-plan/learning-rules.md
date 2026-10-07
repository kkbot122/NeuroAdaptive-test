# Learning rules

Agreed product and safeguard behavior. Numeric policies and implementation details
remain open where noted in [README.md](README.md).

P1 and P2 implemented behavior is in [p1-lifecycle-contract.md](p1-lifecycle-contract.md)
and [p2-grounded-preparation-contract.md](p2-grounded-preparation-contract.md). The
rules below identify implemented behavior and pieces that await later work.

## Activities

| Activity | Eligibility/purpose | Work | Finish condition |
| --- | --- | --- | --- |
| New lesson | New material; no demonstrated prerequisite gap requiring attention | Objective, explanation, example, recap; assess only taught concepts | Teaching covered and assessment graded; weakness can remain |
| Remediation | Demonstrated concept/prerequisite misunderstanding | One focused concept, supported explanation/example, fresh questions | Follow-up graded; selection uses new evidence |
| Targeted practice | Limited or mixed evidence for one concept | Fresh questions without mandatory teaching first | Question set graded |
| Challenge | Understanding demonstrated individually | Application of multiple taught concepts; no outside knowledge required | Question set graded; update assessed concepts only |

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

Selecting prerequisite remediation is a recommendation, not a declaration that the
student lacks knowledge based on source absence. Incomplete/unavailable assessments
must have honest recovery; the ultimate exit policy remains open.

## Assessment and evidence

- P2 implements lesson MCQs through the existing P1 fixed-session lifecycle. Its
  approved, unvalidated policy is 5 questions by default, increasing to cover each
  taught concept, capped at 8 (`P2_ASSESSMENT_*_V1`). Lessons exceeding the bound
  remain recoverably unavailable.
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
- Reporting preserves original evidence; no automatic score change or immediate-resolution
  promise. An authorized reviewer can correct the judgment. Keep the original judgment
  and correction history; recompute affected mastery/attributed outcomes without another
  attempt. Preserve historical decision traces. Access and correction propagation need contracts.

## Mastery and presentation

- Mastery comes from graded concept evidence, never reading, confidence, format switches,
  or learner success buttons. No evidence means Not assessed.
- Multi-concept questions need explicit concept attribution. Wrong challenge answers
  do not erase prior progress or automatically mark every related concept weak.
- Evidence strength and mastery are distinct. Bands/completion criteria require an
  explicit versioned policy; current formulas are unvalidated defaults.
- Presentation preference is separate from mastery. Manual switches are weak preference
  signals; subsequent relevant assessment results inform effectiveness.
- Format changes preserve activity progress. No fixed learning-style identity drives
  the new learning path. Exact attribution and exploration policy remain open.

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
  explanation/questions; never fabricate missing material.

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
