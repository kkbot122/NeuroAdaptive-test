"""
Prerequisite edge proposal: one LLM call over the full normalized concept
list (concept lists are far smaller than raw source text, so this does not
need the bounded-context treatment extraction does), producing candidates
that graph.py then checks and repairs deterministically.
"""
import json
import re
from dataclasses import dataclass
from typing import List
from uuid import UUID

from app.core.prompt_safety import UNTRUSTED_CONTENT_WARNING, wrap_untrusted
from app.modules.curriculum.graph import ProposedEdge
from app.services.generation.gateway import GenerationError, GenerationGateway


class EdgeParseError(Exception):
    pass


@dataclass
class ConceptForEdges:
    id: UUID
    name: str
    definition: str


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z]*\n?", "", stripped)
        stripped = re.sub(r"\n?```$", "", stripped)
    return stripped.strip()


def propose_edges(
    concepts: List[ConceptForEdges], generation: GenerationGateway
) -> List[ProposedEdge]:
    """
    Matches proposed edge names back to concept ids case-insensitively.
    An edge naming a concept not in the list (a hallucinated name) is
    dropped rather than guessed at.
    """
    if len(concepts) < 2:
        return []

    by_name = {c.name.strip().lower(): c.id for c in concepts}
    listing = "\n".join(f"- {c.name}: {c.definition}" for c in concepts)

    prompt = (
        "Concepts in a course, in no particular order:\n\n"
        f"{wrap_untrusted(listing)}\n\n"
        "Propose prerequisite relationships: which concepts should a learner "
        "understand before another one will make sense. Use only the exact "
        "names listed above.\n\n"
        '"strength": "HARD" if the dependent concept is essentially '
        'incomprehensible without the prerequisite; "SOFT" if it merely '
        "helps.\n\n"
        "Respond with ONLY this JSON shape:\n"
        '{"edges": [{"prerequisite": "...", "dependent": "...", '
        '"strength": "HARD"|"SOFT", "confidence": 0.0}]}\n'
        'If no clear prerequisite relationships exist, return {"edges": []}.'
    )

    try:
        raw = generation.generate(
            prompt, system_instruction=UNTRUSTED_CONTENT_WARNING, temperature=0.2, max_output_tokens=3000
        )
        payload = json.loads(_strip_code_fence(raw))
    except (GenerationError, json.JSONDecodeError, ValueError) as exc:
        raise EdgeParseError(str(exc)) from exc

    raw_edges = payload.get("edges")
    if not isinstance(raw_edges, list):
        raise EdgeParseError("Response missing an 'edges' list.")

    edges = []
    for item in raw_edges:
        prereq_name = item.get("prerequisite")
        dep_name = item.get("dependent")
        if not isinstance(prereq_name, str) or not isinstance(dep_name, str):
            continue

        prereq_id = by_name.get(prereq_name.strip().lower())
        dep_id = by_name.get(dep_name.strip().lower())
        if prereq_id is None or dep_id is None or prereq_id == dep_id:
            continue  # hallucinated name, or a nonsensical self-edge

        strength = item.get("strength")
        strength = strength if strength in ("HARD", "SOFT") else "SOFT"

        confidence = item.get("confidence", 0.5)
        confidence = confidence if isinstance(confidence, (int, float)) else 0.5
        confidence = max(0.0, min(1.0, float(confidence)))

        edges.append(
            ProposedEdge(
                prerequisite_id=prereq_id,
                dependent_id=dep_id,
                strength=strength,
                confidence=confidence,
            )
        )
    return edges
