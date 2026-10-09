# P8 subjects and linked earlier courses contract

Status: policy approved 2026-10-10; implementation and automated checks are complete
locally on `codex/p8-linked-courses`. The additive migration has not been applied to the
running local app database. The setup layout was visually inspected at desktop and
393px narrow width; the existing app displayed the catalog-load error because its
running backend does not provide the new P8 catalog response. Hosted behavior and
matching accuracy are not verified.

## Approved product policy

- A subject is optional, belongs to one learner, and groups that learner's courses.
  Subject membership grants no source or evidence access.
- P8 supports one directional earlier-course link per new course. The selected course
  must belong to the learner and be published with an active `READY` version. The link
  stores that exact `CourseVersion.id`; a later active version does not retarget the
  saved link. No implicit traversal through another course's links is allowed.
  Standalone courses remain valid.
- A reliable concept match means equivalent meaning and scope. P8's local deterministic
  check only accepts identical normalized definitions with affirmative support in each
  exact version's source passages and no explicit contradiction or qualification cue in
  either source set. Normalization retains punctuation and Unicode symbols, including
  comparison operators, arithmetic signs, decimal points, and grouping marks. A change
  such as `<` to `>`, `≤` to `≥`, `-5` to `+5`, or `(x + 2) * 3` to `x + (2 * 3)`
  cannot become an equivalent definition. This is a deliberately narrow sufficient condition:
  paraphrases are not accepted as proof. Names and embeddings may identify
  candidates but cannot establish equivalence. A plausible but ambiguous match is
  `UNCERTAIN`; different, contradictory, or unsupported concepts are `UNSUPPORTED`.
  There is no P8 numeric confidence cutoff. Persist both version references, concept IDs,
  source chunk IDs, validator version, status, and rationale. This rule has not been
  calibrated for real-world accuracy and is not a general semantic entailment model.
- Reliable linked evidence is read from its original graded events, with the latest
  approved correction applied. It may supply prerequisite readiness only while the
  corresponding current-course concept has no current-course graded evidence. Current
  evidence takes precedence. Original events, timestamps, judgments, and course
  attribution remain unchanged; evidence is deduplicated by original event ID and is
  never copied into current-course mastery. Existing mastery and recommendation
  formulas and weights stay unchanged. Reading completion never transfers.
- An uncertain match with usable support may offer a genuinely optional, source-grounded
  check in the existing fixed-assessment lifecycle. The check tests the current-course
  concept using only passages from the current and explicitly linked versions. A graded
  answer creates ordinary evidence for that current-course concept. Skipping creates no
  attempt or negative evidence. If usable support is absent, explain the limitation and
  allow the learner to continue. Existing answer submission and feedback gating apply.
  Unsupported matches without usable support are identified in the overview; they do not
  authorize evidence reuse or an optional check, and the learner can continue.
- A link may be changed or removed before the current course is published. Doing so
  invalidates its mappings and unstarted dependent artifacts. Publication freezes the
  relationship for that course version. Deleting the earlier course revokes future
  access and reuse, fences workers, and invalidates unstarted dependent artifacts.
  Unstarted linked preparations and artifacts are invalidated. Started activities and
  fixed assessment sets remain playable from their saved snapshot; citations to deleted
  source material show an unavailable-source notice. Since earlier evidence is never
  copied, deleting its course removes those original events through existing
  course-deletion cleanup.

## Implementation contract

- Persist subjects, one direct course link, the pinned version, and match provenance
  using additive migrations. Scope every read and write to the signed-in owner.
- Reject self-links, foreign or unpublished courses, invalid/non-`READY` versions, and
  cycles. A linked course does not confer access to any of its own linked sources.
- Enforce the exact current-plus-linked course/version scope inside lexical and vector
  queries, again when hydrating source rows, and again in grounding validation. A cited
  passage retains its original course title, document, and page or heading.
- Include the pinned relationship and match/source dependencies in preparation identity
  and worker freshness checks. A stale or revoked dependency cannot commit artifacts.
- Persist the specific uncertain match on each optional-check activity. Preparation may
  use only that mapping's linked concept/source, and workers revalidate it before
  generation and commit.
- Apply existing ownership, correction, fixed-set, tutor restriction, feedback
  withholding, provider-call bounds, and atomic AI-accounting rules.
- Extend course/account deletion to remove owned subjects when unused, links, mappings,
  provenance, and dependent unstarted artifacts without touching another learner's
  records or unrelated courses. Questions whose linked source is deleted remain attached
  to started fixed sets, but are unavailable to new assessments; unstarted linked
  activities and their fixed question memberships are removed.
- Update OpenAPI and regenerate frontend types with the same change.

## Verification boundary

Focused deterministic tests use injected generation/embedding fakes. They can establish
scope enforcement, provenance, lifecycle, and policy behavior for fixtures; they do not
establish real-world matching accuracy or hosted behavior. P9 completion/calibration
work is outside P8. Local verification passed the focused P8 and affected tutor,
preparation, and adaptation regression set (155 tests), 61 frontend tests, frontend
typecheck, lint, OpenAPI contract check, offline Alembic DDL generation, Python compile
check, and `git diff --check`.
The existing app was not rebuilt or migrated; its subjects and published-course picker
could not be functionally exercised there until the local P8 API/database migration is
applied.
