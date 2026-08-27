# NeuroLearn — Frozen Scope

Status: frozen for the one-week working prototype.

This document is the product source of truth. `architecture.md` explains how the system is built, `implementation-plan.md` orders the work, and `AGENTS.md` governs agent behavior.

## Problem statement

Students can ask a generic AI questions about study material, but the AI does not reliably understand the course structure, know what the learner has mastered, identify prerequisite gaps, choose the next useful activity, or prove that its claims are supported by the uploaded sources.

NeuroLearn addresses that gap by converting a learner's material into a source-grounded, adaptive technical course. It maintains concept-level mastery and uncertainty, selects the next activity with a deterministic policy, adapts presentation format, and records enough evidence for an honest pilot evaluation.

## Product definition

NeuroLearn is a self-paced but system-directed course generator and tutor for English-language undergraduate computer-science material.

The frozen loop is:

```text
Google sign-in
→ create course
→ upload syllabus and study material
→ process and index sources asynchronously
→ generate and review course structure and prerequisite graph
→ take or skip an adaptive diagnostic
→ complete a grounded learning activity
→ answer a one-shot assessment
→ update concept mastery and uncertainty
→ receive the next deterministic activity
→ inspect progress and recorded outcomes
```

The one-week prototype must use this real path. It must not contain a preloaded course, document-specific parsing, hard-coded AI output, fake mastery updates, or fake decision traces.

## Actors

### Learner

The only product role. A learner signs in with Google, owns private courses, uploads material, reviews the generated structure, learns, completes assessments, and sees personal progress.

### Project-team evaluator

An authorized team/admin user can view the protected evaluation dashboard containing aggregated and pseudonymized system and pilot results. This is not a teacher-facing course-management role.

## User stories

1. As a learner, I want to sign in with Google so that my courses and progress remain private and persistent.
2. As a learner, I want to upload a syllabus and multiple study files so that NeuroLearn can construct a course from my own material.
3. As a learner without a formal syllabus, I want NeuroLearn to use the material's table of contents or generate a syllabus so that course creation can continue.
4. As a learner, I want visible processing stages so that I know whether extraction, indexing, graph generation, or validation is still running.
5. As a learner, I want failed processing stages to be retryable so that successful work is not repeated unnecessarily.
6. As a learner, I want uncertain extraction and critical content gaps shown to me so that I can understand limitations in the generated course.
7. As a learner, I want to review and edit modules, lessons, concepts, and prerequisite edges so that the generated structure reflects my material.
8. As a learner, I want an adaptive diagnostic so that the system can initialize mastery from evidence.
9. As a learner, I want to skip the diagnostic so that I may start immediately with unknown, high-uncertainty mastery states.
10. As a learner, I want lessons grounded only in my course sources so that unsupported model knowledge is not presented as course content.
11. As a learner, I want document-and-page citations so that I can inspect the origin of an explanation.
12. As a learner, I want the tutor to abstain when the material does not support an answer so that missing evidence is not disguised as knowledge.
13. As a learner, I want a guided activity and a grounded side-chat so that I can learn in sequence and still ask follow-up questions.
14. As a learner, I want concise, detailed, example-driven, analogy-driven, visual, and problem-first presentations so that the system can vary its teaching format.
15. As a learner, I want MCQ, short-answer, numerical, and Python assessments so that different technical skills can be checked.
16. As a learner, I want assessment feedback after my single attempt so that I can understand the expected answer and its source.
17. As a learner, I want concept-level mastery and uncertainty so that limited evidence is not presented with false confidence.
18. As a learner, I want NeuroLearn to select my next activity so that prerequisite gaps and weak concepts guide the course path.
19. As a learner, I want my required syllabus coverage preserved even when the system reorders concepts so that no required topic disappears.
20. As a learner, I want progress and completion gaps shown so that stopping a course is distinguishable from mastering it.
21. As a learner, I want to rate a presentation as not helpful, neutral, or helpful so that future presentation affinity can adapt.
22. As a learner, I want to delete my account and all associated product and pilot data so that I retain control over persistence.
23. As an evaluator, I want deterministic learner scenarios so that recommendation behavior can be verified independently of an LLM.
24. As an evaluator, I want processing, retrieval, generation, sandbox, and end-to-end timings so that system performance can be reported honestly.
25. As an evaluator, I want recommendation-to-outcome traces so that adaptive decisions can be inspected after the fact.
26. As an evaluator, I want pseudonymized pilot drill-downs so that headline results remain auditable without revealing participant identities.

## Functional scope

### Authentication and ownership

- Any Google account may sign in.
- OAuth requests only name, email, profile, and OpenID identity.
- Every course, document, chunk, generated artifact, assessment, attempt, mastery state, object, graph projection, and retrieval query is scoped to the authenticated owner and course.
- Project-team/admin authorization protects evaluation surfaces.

### Course creation and inputs

- Supported domain: English undergraduate computer science.
- Supported formats: PDF, PNG/JPG, TXT, Markdown, DOCX, and PPTX.
- Supported content includes native text, scans, clear handwriting, diagrams, tables, formulas, code, and embedded images on a best-effort basis.
- Per-course limits: one syllabus, up to five study files, approximately 25 MB per file, and 200 pages total.
- If no syllabus is uploaded, use a detected table of contents. If none is usable, generate a syllabus automatically.
- If a required syllabus topic lacks source support, retain it as a visible content gap and do not generate unsupported teaching content.
- Course documents become immutable once the course is created. A learner must create a new course to use different source material.

### Processing pipeline

Processing is asynchronous, durable, idempotent, resumable, and visible through polling.

```text
UPLOADED
→ VALIDATING
→ EXTRACTING
→ INTERPRETING_VISUALS
→ CHUNKING
→ INDEXING
→ EXTRACTING_CONCEPTS
→ BUILDING_GRAPH
→ GENERATING_STRUCTURE
→ VALIDATING_COURSE
→ READY | NEEDS_INPUT | FAILED
```

- Native structured extraction runs first.
- Local OCR handles printed scans.
- A multimodal model handles handwriting, visual regions, and unresolved pages.
- Original assets, page location, extracted structure, and AI interpretation are retained.
- Critical uncertainty blocks readiness; non-critical uncertainty is displayed.
- Failed stages may be retried without duplicating completed outputs.
- Provider quota or availability failure pauses the job for manual retry; there is no automatic provider fallback.
- A draft course version becomes visible as usable only after validation succeeds.

### Course and graph

The hierarchy is:

```text
Course
└── Module
    └── Lesson
        └── Concept
```

- A concept is a small, independently teachable and assessable unit.
- The graph contains directed prerequisite edges only; hierarchy remains relational course structure.
- PostgreSQL is authoritative. Neo4j is a disposable projection for traversal and visualization.
- Graph generation includes confidence, source provenance, and cycle detection.
- Learners may edit topics, module placement, and prerequisite edges before learning.
- Every syllabus topic must remain represented.
- Prerequisite weakness produces a warning and influences scoring but does not block access to a dependent concept.

### Retrieval and grounded generation

- Retrieval is restricted to documents attached to the current learner's current course.
- Structure-aware chunks preserve headings, paragraphs, tables, code blocks, pages, assets, and provenance.
- Retrieval combines lexical search, vector similarity, metadata filters, deduplication, and reranking.
- Lessons, tutor responses, syllabus generation, questions, and explanations operate in source-only mode.
- Each displayed factual answer uses document-and-page citations; internal records retain exact supporting chunk identifiers.
- Citation validation checks existence, ownership, and semantic support.
- If evidence is insufficient, the system responds that the uploaded material does not sufficiently support an answer.
- If sources conflict, the LLM selects one answer and stores the conflict, chosen source, rejected source, rationale, and confidence. Low-confidence resolution abstains.
- Retrieved content is treated as untrusted data: it cannot modify trusted instructions or invoke tools.
- Validated lessons and questions are versioned and cached rather than regenerated on every visit.

### Learning experience

- The learner provides a goal, self-reported starting confidence, priority modules, and optional exclusions. Target dates, schedules, and session-duration planning are excluded.
- The course is self-paced in time but system-directed in path.
- The recommendation engine selects the next activity; the learner cannot override or navigate around it.
- Activity types are `NEW_LESSON`, `PREREQUISITE_REMEDIATION`, `TARGETED_PRACTICE`, `CHALLENGE`, and `RESUME_INTERRUPTED`.
- There is no spaced-review or forgetting-curve behavior.
- The learning workspace combines the selected guided activity with a grounded side-chat.
- Presentation formats are concise, detailed, example-driven, analogy-driven, visual, and problem-first.
- Visual presentation uses source visuals, tables, formulas, code traces, and structured diagrams; AI image generation is excluded.
- An optional three-state helpfulness rating updates presentation affinity. Subsequent assessment correctness also updates that affinity using a weighted moving average.

### Diagnostic and assessments

- The diagnostic adaptively samples foundational and syllabus-important concepts, with a P0 cap of 15 questions.
- Skipping produces `UNKNOWN` mastery with high uncertainty.
- Supported types: MCQ, short answer, numerical, and Python function problems.
- Week-one reliability priority: MCQ and short answer are primary; numerical and Python must execute through real paths but may cover fewer concepts.
- Every question has one attempt and no hints or retries.
- Correctness is binary for mastery.
- MCQ uses the selected option.
- Short-answer correctness is an unrestricted LLM judgment.
- Numerical correctness uses exact string matching.
- Python correctness uses the result returned by self-hosted Judge0 against generated hidden tests.
- Generated Python prompts and tests are trusted after the general separate-model validation pass; reference solutions and mutation tests are not executed before publication.
- A separate call using the same model validates source support, answerability, expected answer, estimated difficulty, and duplicate similarity.
- Difficulty is the generator's LLM-estimated label and is not empirically calibrated.
- After submission, show correctness, expected answer, explanation, and source citation.
- If no valid question can be generated after bounded retries, mark assessment unavailable and leave mastery uncertain.

### Mastery and uncertainty

- Mastery is maintained per learner and concept using the paper's weighted-evidence moving update.
- Unknown concepts have no displayed numeric mastery until evidence exists.
- Binary correctness and LLM-estimated difficulty are the active evidence factors in P0.
- Hint, retry, and learner-confidence factors are absent.
- Response duration is recorded for evaluation but does not affect mastery.
- Independent correct and incorrect answers both reduce uncertainty.
- Repeated identical questions are not independent evidence.
- Default completion threshold: mastery at least `0.80` and uncertainty at most `0.30` for every required syllabus concept.
- A learner may stop earlier; the course is then ended with visible mastery gaps, not completed.

### Recommendation engine

- Candidate selection and scoring are deterministic; the LLM never chooses the next activity.
- Score features are expected gain, prerequisite readiness, goal relevance, presentation affinity, difficulty fit, recent repetition penalty, and already-high-mastery penalty.
- Prerequisites always remain eligible but generate warnings and lower readiness.
- Fixed tie-breaking priority:

```text
PREREQUISITE_REMEDIATION
→ RESUME_INTERRUPTED
→ TARGETED_PRACTICE
→ NEW_LESSON
→ CHALLENGE
```

- The learner sees the selected activity without a generated explanation.
- The protected trace stores all candidate scores, selected activity, reason features, subsequent assessment, and mastery change.

### Data lifecycle and security boundary

- Original files and derived assets use private object storage and short-lived signed URLs.
- Only the processing worker receives direct file access. Other services receive identifiers or bounded payloads.
- Uploaded files are checked by extension only in P0; file signatures, malware scanning, archive/decompression defenses, and comprehensive malicious-upload protection are excluded.
- Python runs with no network and no access to application secrets, user files, or product databases.
- Per-user quotas limit active processing, courses, uploads, tutor requests, regeneration, and code execution.
- Operational logs contain metadata, identifiers, timings, token usage, and error categories—not document text, answers, OAuth tokens, or signed URLs.
- Free-tier third-party AI processing is disclosed to every user and pilot participant.
- Account deletion removes files, chunks, graphs, generated content, attempts, mastery, traces, and pilot records.

## Evaluation scope

### Real pilot

- Recruit 10–15 consenting participants.
- Each participant uses their own material in two sessions across several days.
- NeuroLearn automatically generates different pre-test and post-test questions from the same blueprint.
- A separate validation call estimates equivalence of concept coverage, difficulty, and required reasoning.
- Report each participant separately; do not compute a pooled group learning result.
- Primary metric per participant: post-test percentage minus pre-test percentage.
- Permitted conclusion: “The participant's generated post-test score was higher/lower/unchanged compared with their generated pre-test score after using NeuroLearn.”
- Do not claim causation, superiority to fixed sequencing, validated mastery accuracy, or population-level learning improvement.

### System evaluation

- Predefined simulated learner profiles verify deterministic recommendation outputs and reason codes.
- Automated citation-validator labels produce an explicitly labelled automated supported-claim precision and unsupported-claim rate.
- Concept-graph evaluation covers only structural validity, syllabus mapping, cycles, and learner acceptance—not semantic prerequisite accuracy.
- Multimodal evaluation covers successful acceptance and user-perceived usefulness—not extraction accuracy.
- Security evaluation covers prompt injection, cross-user retrieval, and Python sandbox escape attempts.
- Performance evaluation records upload processing, retrieval, tutor, question-generation, Judge0, and end-to-end latency plus token usage and retry/failure counts.
- Pass thresholds are frozen before running the pilot.
- The protected dashboard shows aggregated or pseudonymized results and allows authorized drill-down into anonymized cases, citations, decisions, and outcomes.

## Week-one pass condition

The golden path must work on at least two previously unseen supported document sets without code changes or seeded course knowledge:

1. One native-text set.
2. One set containing at least one scanned or visual page.

Both must complete sign-in, upload, asynchronous processing, structure/graph review, diagnostic, grounded lesson, assessment, mastery update, deterministic recommendation, progress, and trace inspection.

## Explicit limitations and out of scope

- Production-grade reliability across arbitrary CS documents.
- Guaranteed handwriting, diagram, table, formula, or OCR accuracy.
- Human-verified citation precision.
- Human-verified prerequisite correctness.
- Validated short-answer grading accuracy.
- Robust numerical equivalence or unit handling.
- Generated Python test-quality verification.
- Learner override of the recommended activity.
- Time-based planning, calendars, target dates, session fitting, forgetting curves, and spaced review.
- Multiple teachers, course publishing, enrolment, collaboration, or admin course authoring.
- Web-grounded or general-knowledge tutoring.
- Document changes after course creation.
- Antivirus scanning, file-signature validation, decompression-bomb defenses, and a complete malicious-upload threat model.
- Causal claims about learning improvement.
- Provider failover.
- Mobile apps, offline mode, PWA behavior, notifications, and payments.

## Post-week milestones

The real pilot, broad ingestion benchmark, performance experiments, extensive prompt-injection suite, sandbox attack testing, arbitrary-document hardening, final research results, and provider-quota hardening follow the working vertical prototype.
