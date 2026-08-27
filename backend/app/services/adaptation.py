"""
Neuro-Adaptive Learning Engine
================================
Continuous vector-driven personalization. No archetypes. No labels.
No hardcoded if/else trees for personalization logic.

All adaptation is computed via weighted dot-products, soft scaling (tanh),
and ranked directive selection — analogous to a recommendation score.
"""

import math
from typing import NamedTuple, Optional

from openai import AsyncOpenAI

from app.core.config import settings

GROQ_BASE_URL = "https://api.groq.com/openai/v1"

_client: Optional[AsyncOpenAI] = None


def get_client() -> AsyncOpenAI:
    """
    Build the Groq client on first use rather than at import time.

    Constructing it at module scope made importing this module — and so
    importing the whole app — fail whenever no API key was present, which broke
    tests, Alembic autogeneration, and any offline tooling. It also read
    os.getenv directly, which does not consult backend/.env the way
    pydantic-settings does, so it only ever worked because Compose injects the
    variable into the real environment.
    """
    global _client
    if _client is None:
        _client = AsyncOpenAI(base_url=GROQ_BASE_URL, api_key=settings.GROQ_API_KEY)
    return _client


# ─── 1. VECTOR NORMALIZATION ──────────────────────────────────────────────────
# raw_scores: {visual, structural, active, logic} ∈ [0, 100]
# Normalized to [-1, +1], then soft-scaled via tanh to compress extremes.

def _norm(v: float) -> float:
    """Map [0, 100] → [-1, +1] linearly. Clamps input to valid range."""
    return (max(0.0, min(100.0, v)) / 50.0) - 1.0


def _soft(x: float, k: float = 2.0) -> float:
    """Soft-scale via tanh: preserves sign, compresses extremes toward ±1."""
    return math.tanh(k * x)


def normalize_profile(raw: dict) -> dict:
    """Convert raw_scores dict to soft-normalized cognitive vector [-1, +1]."""
    return {
        dim: _soft(_norm(raw.get(dim, 50.0)))
        for dim in ("visual", "structural", "active", "logic")
    }


# ─── 2. DIRECTIVE LIBRARY ────────────────────────────────────────────────────
# Each Directive has: a compressed instruction string + a weights dict.
# weights = {dimension: affinity} where +1.0 means "use when dim is high",
# -1.0 means "use when dim is low".
#
# Selection: relevance = dot(directive.weights, profile)
# Directives with relevance > GATE_THRESHOLD are included (soft gate).

GATE_THRESHOLD = 0.25  # tune between 0.15–0.40


class Directive(NamedTuple):
    text: str
    weights: dict  # {dimension: affinity_float}


# Raw-score directives (used when FSLSM vectors are neutral)
RAW_DIRECTIVES: list[Directive] = [
    Directive(
        "ASCII diagrams, comparison tables, spatial analogies.",
        {"visual": +1.0},
    ),
    Directive(
        "Dense precise text; skip decorative visuals.",
        {"visual": -1.0},
    ),
    Directive(
        "Open with executive overview, then drill into detail.",
        {"structural": +1.0},
    ),
    Directive(
        "Lead with specifics; no preamble or broad intro.",
        {"structural": -1.0},
    ),
    Directive(
        "Number every reasoning step; include formulas and code.",
        {"logic": +1.0, "structural": +0.3},
    ),
    Directive(
        "End with a hands-on task or Socratic question.",
        {"active": +1.0},
    ),
    Directive(
        "Worked examples with intermediate steps shown.",
        {"active": +0.6, "logic": +0.4},
    ),
    Directive(
        "Real-world analogies and concrete connections.",
        {"visual": +0.4, "structural": +0.4},
    ),
]

# FSLSM directives (used when FSLSM vectors have meaningful signal)
FSLSM_DIRECTIVES: list[Directive] = [
    Directive(
        "Theoretical depth, multiple perspectives, reflective summaries.",
        {"processing": +1.0},
    ),
    Directive(
        "Immediate hands-on exercises; avoid lengthy theory blocks.",
        {"processing": -1.0},
    ),
    Directive(
        "Real data, established facts, concrete procedures.",
        {"perception": -1.0},
    ),
    Directive(
        "Highlight patterns, relationships, conceptual 'why'.",
        {"perception": +1.0},
    ),
    Directive(
        "ASCII diagrams, comparison grids, spatial layout.",
        {"reception": -1.0},
    ),
    Directive(
        "Rich annotated written explanations over visual gimmicks.",
        {"reception": +1.0},
    ),
    Directive(
        "Strict linear steps; fully master each before the next.",
        {"understanding": -1.0},
    ),
    Directive(
        "Open with the big picture; show how pieces connect to the whole.",
        {"understanding": +1.0},
    ),
]


def _directive_score(d: Directive, profile: dict) -> float:
    """Continuous relevance: dot product of directive weights with user profile."""
    return sum(profile.get(dim, 0.0) * w for dim, w in d.weights.items())


def select_directives(
    profile: dict,
    library: list[Directive],
    max_n: int = 3,
) -> list[str]:
    """
    Rank directives by relevance score (dot product), apply soft gate,
    return top-N instruction strings. No hard branching.
    """
    scored = sorted(
        [(d, _directive_score(d, profile)) for d in library],
        key=lambda x: x[1],
        reverse=True,
    )
    active = [d.text for d, score in scored if score > GATE_THRESHOLD]
    if not active:
        return ["Balance structure with depth; mix text with clarity."]
    return active[:max_n]


# ─── 3. PROMPT GENERATORS ────────────────────────────────────────────────────
# Targets ~80–110 tokens per system prompt. No redundant phrasing.

_FORMAT_RULES = (
    "Rules: ## headings, **bold** terms, `code` literals, "
    "bullet lists ≤4/para, fenced ```lang blocks. "
    "Structure: hook→content→summary. "
    "Tone: precise, encouraging. Adapt silently."
)


def generate_system_prompt(raw_scores: dict) -> str:
    """
    Token-efficient prompt from continuous raw_scores [0, 100].
    Uses weighted directive selection — no if/else personalization.
    """
    profile = normalize_profile(raw_scores)
    directives = select_directives(profile, RAW_DIRECTIVES)
    style = " ".join(directives)
    return (
        f"You are a Neuro-Adaptive Learning Assistant.\n"
        f"Learner style: {style}\n"
        f"{_FORMAT_RULES}"
    )


def build_fslsm_system_prompt(vectors: dict) -> str:
    """
    Generate prompt from FSLSM continuous vectors [-1, +1].
    Applies light soft-scaling (k=1.5) since vectors are already normalized.
    """
    profile = {dim: _soft(float(v), k=1.5) for dim, v in vectors.items()}
    directives = select_directives(profile, FSLSM_DIRECTIVES)
    style = " ".join(directives)
    return (
        f"You are a Neuro-Adaptive Learning Assistant (FSLSM-calibrated).\n"
        f"Learner style: {style}\n"
        f"{_FORMAT_RULES}"
    )


def build_adaptive_system_prompt(raw_scores: dict) -> str:
    """Legacy entry-point alias. Delegates to generate_system_prompt."""
    return generate_system_prompt(raw_scores)


# ─── 4. BEHAVIORAL SIGNAL INFERENCE ──────────────────────────────────────────
# Infers learning behavior signals from chat prompt text (keyword heuristics).
# Maps signals to raw_score micro-deltas. No ML needed — lightweight and fast.

_PROMPT_SIGNALS: list[tuple[list[str], str]] = [
    (["diagram", "chart", "draw", "visualize", "picture", "graph"], "requested_diagram"),
    (["show me", "example", "demonstrate", "illustrate"],           "requested_example"),
    (["summary", "summarize", "tldr", "overview", "brief"],        "requested_summary"),
    (["step by step", "step-by-step", "walkthrough", "how to", "how do i"], "requested_steps"),
    (["why", "reason", "purpose", "what's the point"],             "prompt_why"),
    (["big picture", "overall", "in general", "at a high level"], "prompt_big_picture"),
]

BEHAVIORAL_SIGNAL_DELTAS: dict[str, dict[str, float]] = {
    "requested_diagram":   {"visual": +5},
    "requested_example":   {"active": +4, "logic": +3},
    "requested_summary":   {"structural": +5},
    "requested_steps":     {"logic": +5},
    "prompt_why":          {"structural": +3, "logic": +2},
    "prompt_big_picture":  {"structural": +4},
    # Time/depth signals (applied externally)
    "long_read":           {"logic": +3, "structural": +2},
    "quick_skim":          {"active": +3, "structural": -2},
    # RLHF signals
    "prefer_visual":       {"visual": +4},
    "prefer_text":         {"visual": -4},
    "prefer_overview":     {"structural": +4},
    "prefer_details":      {"structural": -4},
    "prefer_interactive":  {"active": +4},
    "prefer_stepwise":     {"logic": +4},
}


def infer_signals_from_prompt(prompt: str) -> list[str]:
    """
    Lightweight keyword scan of user prompt → list of signal names.
    Called on every chat message in the router.
    """
    p = prompt.lower()
    return [signal for keywords, signal in _PROMPT_SIGNALS if any(k in p for k in keywords)]


def apply_signals_to_scores(scores: dict, signals: list[str]) -> dict:
    """
    Apply a list of inferred signal names as micro-deltas to raw_scores.
    Deltas are small (±3 to ±5) to prevent hard jumps. Clamps to [0, 100].
    """
    updated = dict(scores)
    for sig in signals:
        for dim, delta in BEHAVIORAL_SIGNAL_DELTAS.get(sig, {}).items():
            updated[dim] = max(0.0, min(100.0, updated.get(dim, 50.0) + delta))
    return updated


def update_scores_from_feedback(scores: dict, signal: str) -> dict:
    """
    RLHF micro-feedback: apply a named signal delta to raw_scores.
    Designed for explicit user feedback (e.g., 'prefer_visual').
    """
    return apply_signals_to_scores(scores, [signal])


# ─── 5. CALIBRATION ENGINE ───────────────────────────────────────────────────
# Scenario-based triage questions. Each answer maps to vector deltas.
# No discrete categories — outputs feed directly into raw_scores.

CALIBRATION_QUESTIONS: list[dict] = [
    {
        "id": "furniture",
        "text": "New furniture arrives with no instructions. You:",
        "options": {
            "A": {"text": "Study all parts laid out visually first",   "delta": {"visual": +15, "structural": +8}},
            "B": {"text": "Follow the numbered booklet step by step",  "delta": {"logic": +15, "structural": +10}},
            "C": {"text": "Start assembling and fix mistakes as you go", "delta": {"active": +20, "structural": -8}},
            "D": {"text": "Watch an assembly video online",            "delta": {"visual": +10, "active": +8}},
        },
    },
    {
        "id": "lecture",
        "text": "You join a lecture 10 min late. You:",
        "options": {
            "A": {"text": "Ask someone for a quick summary",          "delta": {"structural": +12, "active": +5}},
            "B": {"text": "Follow along from wherever they are",      "delta": {"active": +15, "logic": +5}},
            "C": {"text": "Review slides to catch up before listening", "delta": {"visual": +12, "structural": +8}},
            "D": {"text": "Take notes and piece it together later",   "delta": {"logic": +12, "structural": +5}},
        },
    },
    {
        "id": "unfamiliar",
        "text": "Learning an unfamiliar concept, you prefer:",
        "options": {
            "A": {"text": "A diagram showing all relationships",          "delta": {"visual": +20}},
            "B": {"text": "A worked example with annotations",           "delta": {"logic": +15, "visual": +5}},
            "C": {"text": "A summary of key takeaways first",           "delta": {"structural": +20}},
            "D": {"text": "A challenge problem to solve immediately",   "delta": {"active": +20}},
        },
    },
]


def process_calibration_answers(answers: dict[str, str]) -> dict[str, float]:
    """
    Convert {question_id: option_key} answers to cumulative dimension deltas.
    Returns: {dim: total_delta} to be added to baseline raw_scores.
    """
    totals: dict[str, float] = {"visual": 0.0, "structural": 0.0, "active": 0.0, "logic": 0.0}
    q_by_id = {q["id"]: q for q in CALIBRATION_QUESTIONS}

    for qid, chosen_key in answers.items():
        q = q_by_id.get(qid)
        if not q:
            continue
        option = q["options"].get(chosen_key.upper())
        if not option:
            continue
        for dim, delta in option["delta"].items():
            totals[dim] = totals.get(dim, 0.0) + delta

    return totals


# ─── 6. SCORING ENGINE ───────────────────────────────────────────────────────
# Format effectiveness score from multi-signal learning outcomes.
# Used to continuously reinforce or attenuate active dimension weights.

def compute_format_effectiveness(
    accuracy: float,    # [0, 1] — quiz/task accuracy
    completion: float,  # [0, 1] — session completion rate
    engagement: float,  # [0, 1] — interaction depth (messages, follow-ups)
    feedback: float,    # [0, 1] — explicit user feedback
) -> float:
    """Weighted composite effectiveness. Higher = format resonated with learner."""
    return (
        accuracy    * 0.5 +
        completion  * 0.2 +
        engagement  * 0.2 +
        feedback    * 0.1
    )


def reinforce_scores_from_effectiveness(
    scores: dict,
    effectiveness: float,
    active_dimensions: list[str],
    learning_rate: float = 0.03,
) -> dict:
    """
    Continuously adjust raw_scores based on format effectiveness.
    effectiveness > 0.5 → reinforce active dimensions (they worked).
    effectiveness < 0.5 → attenuate active dimensions (they didn't).
    learning_rate: small value prevents large jumps. Tune 0.01–0.05.
    """
    updated = dict(scores)
    # Maps [0,1] effectiveness → adjustment ∈ [-5, +5]
    adjustment = learning_rate * (effectiveness - 0.5) * 100
    for dim in active_dimensions:
        updated[dim] = max(0.0, min(100.0, updated.get(dim, 50.0) + adjustment))
    return updated


# ─── 7. QUIZ INSTRUCTIONS ────────────────────────────────────────────────────
# Injected into every chat system prompt as a protocol addendum.

QUIZ_INSTRUCTIONS = """

━━━ ADAPTIVE QUIZ GENERATION PROTOCOL ━━━

WHEN TO GENERATE A QUIZ — all three conditions must be met:
1. The learner has asked at least 3 substantive questions about a specific topic in this session.
2. You have explained the topic in meaningful depth across multiple messages.
3. The learner explicitly asks to be tested, OR the conversation has clearly reached a natural review point.

NEVER generate a quiz:
• On greetings, small talk, or first messages ("hello", "hi", "what can you do").
• After only 1–2 exchanges.
• When the learner is still asking clarifying questions.
• When the learner has not yet demonstrated understanding of the topic.

QUIZ RULES (when generation is appropriate):
• Generate exactly 5 to 7 MCQ questions — never fewer.
• Distribute difficulty: ~2 easy (recall), ~2 medium (application), ~1-2 hard (analysis/synthesis).
• Each question must test a DISTINCT concept or sub-topic (no repeats).
• Include ONE question that tests visual/spatial reasoning if the topic allows.
• Include ONE question that tests step-by-step logic/calculation if the topic allows.

OUTPUT FORMAT — place the quiz at the END of your message, formatted exactly like this:

<quiz>
{
  "title": "Short descriptive quiz title",
  "questions": [
    {
      "question": "Full question text?",
      "topic": "Specific sub-topic being tested",
      "difficulty": "easy|medium|hard",
      "options": ["Option A", "Option B", "Option C", "Option D"],
      "correct_answer": "Option A",
      "explanation": "Clear explanation of why this answer is correct."
    }
  ]
}
</quiz>

DO NOT list the questions inline in your message. Put ALL quiz content inside <quiz> tags only.
"""


# ─── 8. LEGACY ARCHETYPE COMPAT ──────────────────────────────────────────────

def archetype_to_scores(archetype: str) -> dict:
    """Map legacy archetype strings to raw_scores for backward compatibility."""
    _map = {
        "THE_PIONEER":             {"visual": 50, "structural": 50, "active": 50, "logic": 50},
        "THE_VISUAL_ARCHITECT":    {"visual": 90, "structural": 80, "active": 40, "logic": 40},
        "THE_DEEP_SCHOLAR":        {"visual": 30, "structural": 90, "active": 20, "logic": 90},
        "THE_STRATEGIC_SKIMMER":   {"visual": 40, "structural": 20, "active": 20, "logic": 40},
        "THE_LOGICAL_TINKERER":    {"visual": 20, "structural": 40, "active": 80, "logic": 100},
        "THE_ADAPTIVE_GENERALIST": {"visual": 60, "structural": 60, "active": 60, "logic": 60},
        "THE_VISUALIZER":          {"visual": 90, "structural": 50, "active": 40, "logic": 30},
        "THE_ARCHITECT":           {"visual": 40, "structural": 90, "active": 30, "logic": 70},
        "THE_SPRINTER":            {"visual": 40, "structural": 30, "active": 90, "logic": 40},
        "THE_DEBUGGER":            {"visual": 30, "structural": 40, "active": 40, "logic": 90},
    }
    return _map.get(archetype, _map["THE_PIONEER"])


# ─── 9. STANDALONE ASYNC GENERATION (legacy compat) ─────────────────────────

async def generate_adapted_response(
    user_query: str,
    raw_scores: dict,
    chat_history: Optional[list] = None,
) -> str:
    system_prompt = generate_system_prompt(raw_scores)
    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    if chat_history:
        messages.extend(chat_history)
    messages.append({"role": "user", "content": user_query})

    try:
        response = await get_client().chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            temperature=0.7,
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"Groq API Error: {e}")
        return "I'm experiencing neural interference right now. Please try again."


class AdaptationService:
    async def adapt_content(self, original_text: str, raw_scores: dict) -> str:
        query = "Adapt the following text for me:\n\n" + original_text
        return await generate_adapted_response(user_query=query, raw_scores=raw_scores)


adaptation_service = AdaptationService()
