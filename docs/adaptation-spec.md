# Adaptation Spec — mastery, readiness, recommendation

Transcribed from the paper (§VII-A–C) supplied 2026-08-28, then reconciled
against `frozen-scope.md`, which is the product source of truth for what P0
actually ships.

**Read the conflicts section before implementing.** The paper specifies more
than P0 builds, and in two places specifies the opposite of what frozen scope
requires. Nothing here is silently resolved.

Every constant below is a **named, versioned, configurable default and is not
empirically calibrated** (AGENTS.md §1). The paper itself says so of its own
weights: "the specification's stated defaults, not fitted values".

---

## 1. Evidence value — paper Eq. 1

```
e_a = x_a · w_d · w_t · p_h · y_h · s_a
```

| Symbol | Meaning | Paper's illustrative values | **P0** |
|---|---|---|---|
| `x_a` | raw correctness ∈ [0,1] | — | **active** (binary) |
| `w_d` | difficulty weight | 0.80 easy / 1.00 medium / 1.20 hard | **active** (LLM-estimated) |
| `w_t` | independence weight — answered without external aid | — | **neutral 1.0** |
| `p_h` | hint-usage penalty | 0.75 one hint, 0.50 multiple, 1.0 unaided | **neutral 1.0** |
| `y_h` | attempt-decay weight | — | **neutral 1.0** |
| `s_a` | learner self-reported confidence adjustment | — | **neutral 1.0** |

frozen-scope.md: *"Binary correctness and LLM-estimated difficulty are the
active evidence factors in P0. Hint, retry, and learner-confidence factors are
absent."* So P0 reduces to `e_a = x_a · w_d`.

Implement the full six-factor product with the four inactive factors as named
constants pinned to 1.0, rather than a two-factor shortcut — enabling one later
is then a config change, not a rewrite of the update.

## 2. Mastery update — paper Eq. 2

```
m_c(t+1) = ( m_c(t) · w_prior + e_a · w_evidence ) / ( w_prior + w_evidence )
```

The paper explicitly leaves `w_prior` and `w_evidence` **"as an implementation
parameter"**. They are ours to choose and must be labelled unvalidated.

**Chosen defaults:** `w_prior = 3.0`, `w_evidence = 1.0` — one observation moves
mastery by a quarter of the gap to the evidence value, so a single answer
matters without dominating. This directly serves the paper's requirement that
*"a single incorrect answer is explicitly required not to produce a sharp
mastery reduction"*.

The paper lists factors that should temper a wrong answer (item ambiguity,
accidental selection, time pressure, prior consistent success, recurring
misconceptions) and then states it **"gives no closed-form adjustment for these
factors, and we do not invent one"**. Neither do we. The damping above is the
whole mechanism in P0; the listed factors are not modelled.

## 3. Uncertainty

Required: an explicit `u_c ∈ [0,1]` that **decreases with the number of
independent evidence observations**, so state never collapses to a point
estimate. No closed form is given in the paper.

**Chosen default:** `u_c = 1 / √(1 + n)`, `n` = count of independent
observations for the concept. Satisfies the stated property; unvalidated.

Repeated identical questions are **not** independent evidence
(frozen-scope.md), so `n` counts distinct questions, not attempts.

## 4. Reported state — paper Table IV

| Mastery | Reported state |
|---|---|
| unknown / high uncertainty | Not assessed |
| 0.00–0.30 | Needs attention |
| 0.40–0.60 | Developing |
| 0.60–0.80 | Proficient |
| 0.80–1.00 | Mastered |

**The published bands are not a partition** — there is a gap (0.30–0.40) and
overlaps at 0.60 and 0.80. Implemented as half-open intervals covering [0,1]
with no gap, boundaries resolving upward:

```
u_c high or no evidence  -> Not assessed
[0.00, 0.40)             -> Needs attention
[0.40, 0.60)             -> Developing
[0.60, 0.80)             -> Proficient
[0.80, 1.00]             -> Mastered
```

## 5. Prerequisite readiness — paper Eq. 3

```
R(c) = min over p in P(c) of  m̂_p  ·  φ(c)
```

The weakest prerequisite governs. A concept enters the candidate set when
`R(c) ≥ θ_ready`.

frozen-scope.md: prerequisite weakness *"produces a warning and influences
scoring but does not block access"*. So `θ_ready` gates **candidate
generation**, never learner access.

## 6. Candidate scoring — paper Eq. 4

```
score(a) = 0.30·g(a) + 0.20·R(a) + 0.15·ρ(a) + 0.15·r(a) + 0.10·τ(a) + 0.10·η(a)
           − δ(a) − π(a)
```

| Term | Meaning | **P0** |
|---|---|---|
| `g` | expected learning gain | active, 0.30 |
| `R` | prerequisite readiness (Eq. 3) | active, 0.20 |
| `ρ` | relevance to stated goal | active, 0.15 |
| `r` | urgency — proximity to scheduled review / target date | **disabled — see C-1** |
| `τ` | fit for available session time | **disabled — see C-1** |
| `η` | presentation-affinity effectiveness | active, 0.10 |
| `δ` | difficulty-mismatch penalty | active |
| `π` | repetition penalty after rejection | active |

Tie-break order is fixed by frozen-scope.md and is **not** part of the score:

```
PREREQUISITE_REMEDIATION → RESUME_INTERRUPTED → TARGETED_PRACTICE
→ NEW_LESSON → CHALLENGE
```

## 7. Conflicts with frozen-scope.md — declared, not resolved silently

**C-1 — `r` and `τ` cannot exist in P0.** Eq. 4 weights urgency (0.15) and
session-time fit (0.10), and Algorithm 1 takes `τ_max` session minutes. But
frozen-scope.md excludes *"time-based planning, calendars, target dates,
session fitting, forgetting curves, and spaced review"* and states *"there is
no spaced-review or forgetting-curve behavior"*.

Handling: both disabled. Their combined 0.25 is **redistributed
proportionally across the surviving positive terms**, so the maximum score
stays 1.0 and relative weighting is preserved:

```
g 0.30 -> 0.400    R 0.20 -> 0.267    ρ 0.15 -> 0.200    η 0.10 -> 0.133
```

Recorded as a derived constant with the original weights kept alongside, so
re-enabling either term restores the published values exactly. Flagged for
review: leaving them at published weights with a 0.75 ceiling was the
alternative, and it makes cross-candidate comparison no different — this choice
only affects the absolute number shown.

**C-2 — natural-language explanation.** Algorithm 1 line 9 returns the
activity *"with natural-language explanation"*. frozen-scope.md: *"The learner
sees the selected activity without a generated explanation."*

Handling: **frozen-scope wins** (AGENTS.md §1 authority order; it is the
product source of truth). The reason codes and full candidate scores are still
persisted on `AdaptationDecision` for the protected trace — they are simply not
rendered to the learner.

**C-3 — Algorithm 1's `τ_max` parameter** is dropped for the same reason as
C-1.

## 8. What must be pure

Per the build mandate and architecture.md, both of these have **no network and
no database call inside them**:

```
apply_evidence(previous_state, correctness, difficulty) -> mastery, uncertainty, evidence_record
recommend(learner_state, course_state, activity_history) -> selected, candidate_scores, reason_features
```

Persistence and presentation wrap these seams. `AdaptationDecision` is written
**before** the recommendation is returned to any caller.
