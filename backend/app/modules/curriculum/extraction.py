"""
Concept extraction: per-section LLM proposal of candidate concepts.

Run per document section (this phase groups by a document's heading_path,
already computed by Phase 1's chunker) with bounded context, never as one
whole-document prompt -- the mandate's specific instruction, and the reason
being the same one that motivates 500-800 token chunks in the first place:
a model asked to summarize an entire 90-page document in one call produces
fluent structure with weak grounding to any specific part of it.

Source-chunk granularity for this phase is section-level, not per-sentence:
a concept extracted from a section is linked to every chunk in that section
group. Precise which-exact-chunk-supports-which-exact-concept is a nice-to-
have this phase does not attempt; ConceptSource still resolves to real, owned
chunks either way, which is what validation.py actually checks.
"""
import json
import re
from typing import Dict, List

from app.modules.curriculum.normalization import CandidateConcept
from app.modules.documents.chunk_models import Chunk
from app.services.embedding.gateway import EmbeddingGateway
from app.services.generation.gateway import GenerationError, GenerationGateway

MAX_SECTION_CHARS = 6000  # bounded-context ceiling for one extraction call


class ExtractionParseError(Exception):
    """The model's response could not be parsed as the expected shape."""


def group_chunks_into_sections(chunks: List[Chunk]) -> List[List[Chunk]]:
    """
    Groups chunks by heading_path, preserving document order. A chunk with no
    heading_path (a document with no detected structure) becomes its own
    single-chunk group rather than being silently dropped.
    """
    groups: Dict[str, List[Chunk]] = {}
    order: List[str] = []
    for chunk in sorted(chunks, key=lambda c: (str(c.document_id), c.position)):
        key = chunk.heading_path or f"__no_heading__:{chunk.id}"
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(chunk)
    return [groups[key] for key in order]


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z]*\n?", "", stripped)
        stripped = re.sub(r"\n?```$", "", stripped)
    return stripped.strip()


def propose_concepts_for_section(
    section_chunks: List[Chunk],
    generation: GenerationGateway,
    embeddings: EmbeddingGateway,
) -> List[CandidateConcept]:
    """One bounded-context LLM call, then one embedding call per proposed
    concept (needed for normalization's similarity comparison)."""
    if not section_chunks:
        return []

    text = "\n\n".join(c.text for c in section_chunks)[:MAX_SECTION_CHARS]
    heading = section_chunks[0].heading_path or "(untitled section)"

    prompt = (
        f"Section: {heading}\n\n{text}\n\n"
        "Identify the distinct, independently teachable concepts in this "
        "section. For each, give a name, a self-contained one-to-two "
        "sentence definition grounded ONLY in this text, an importance score "
        "in [0,1], and a Bloom's taxonomy level "
        "(remember|understand|apply|analyze|evaluate|create).\n\n"
        "Respond with ONLY this JSON shape:\n"
        '{"concepts": [{"name": "...", "definition": "...", '
        '"importance": 0.0, "bloom_level": "..."}]}\n'
        "If the section contains no distinct teachable concept, return "
        '{"concepts": []}.'
    )

    try:
        raw = generation.generate(prompt, temperature=0.2, max_output_tokens=2000)
        payload = json.loads(_strip_code_fence(raw))
    except (GenerationError, json.JSONDecodeError, ValueError) as exc:
        raise ExtractionParseError(str(exc)) from exc

    concepts = payload.get("concepts")
    if not isinstance(concepts, list):
        raise ExtractionParseError("Response missing a 'concepts' list.")

    chunk_ids = [c.id for c in section_chunks]
    candidates = []
    for item in concepts:
        name = item.get("name")
        definition = item.get("definition")
        if not isinstance(name, str) or not isinstance(definition, str) or not name.strip():
            continue  # skip a malformed entry rather than fail the whole section

        importance = item.get("importance", 0.5)
        importance = importance if isinstance(importance, (int, float)) else 0.5
        importance = max(0.0, min(1.0, float(importance)))

        embedding = embeddings.embed_texts([definition])[0]

        candidates.append(
            CandidateConcept(
                name=name.strip(),
                definition=definition.strip(),
                source_chunk_ids=list(chunk_ids),
                embedding=embedding,
                importance=importance,
                bloom_level=item.get("bloom_level") if isinstance(item.get("bloom_level"), str) else None,
            )
        )
    return candidates
