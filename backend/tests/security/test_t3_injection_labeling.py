"""
T3 structural test (mandate test case 8): inspect the LITERAL prompt payload
sent to the model for every generation surface, not just the code that is
supposed to build it correctly. Deterministic, no live model call -- this
proves the delimiting/labeling discipline is wired in, which is a
precondition for the defense working at all. It does NOT measure whether a
real model actually resists a payload; that is docs/SECURITY.md's job (see
scripts/measure_injection_resistance.py for the live-measured numbers).
"""
import uuid

from app.core.prompt_safety import UNTRUSTED_CONTENT_WARNING
from app.modules.curriculum.edges import ConceptForEdges, propose_edges
from app.modules.curriculum.extraction import propose_concepts_for_section
from app.modules.curriculum.normalization import CandidateConcept, NormalizedConcept, _adjudicate
from app.modules.documents.chunk_models import Chunk
from app.modules.tutor.prompt import ReferenceChunk, SYSTEM_INSTRUCTION, build_tutor_prompt
from app.services.embedding.fake import FakeEmbeddingGateway
from app.services.generation.fake import FakeGenerationGateway
from tests.security.injection_payloads import INJECTION_PAYLOADS


def make_chunk(text: str) -> Chunk:
    return Chunk(
        id=uuid.uuid4(), document_id=uuid.uuid4(), course_id=uuid.uuid4(), owner_id=1,
        position=0, heading_path="Intro", text=text,
    )


class TestExtractionDelimitsUntrustedText:
    def test_every_payload_is_wrapped_and_the_system_warning_is_sent(self):
        gen = FakeGenerationGateway().set_default('{"concepts": []}')
        embeddings = FakeEmbeddingGateway()
        for payload in INJECTION_PAYLOADS:
            chunk = make_chunk(f"Some real course content. {payload.text} More real content.")
            propose_concepts_for_section([chunk], gen, embeddings)

        assert len(gen.calls) == len(INJECTION_PAYLOADS)
        for prompt, system_instruction, payload in zip(gen.calls, gen.system_instructions, INJECTION_PAYLOADS):
            assert "<<UNTRUSTED CONTENT" in prompt
            assert "<<END UNTRUSTED CONTENT>>" in prompt
            assert payload.text in prompt  # the payload IS sent (extraction must read real content)...
            # .index() finds the FIRST match and .rindex() the LAST -- needed
            # because the delimiter-breaking payload family deliberately
            # embeds fake "<<END UNTRUSTED CONTENT>>" text as its attack, so
            # a naive first-match search can find the attacker's fake close
            # tag before the real one. The real opening delimiter is always
            # the leftmost text in the wrapped block (nothing precedes it);
            # the real closing delimiter is always the rightmost (appended
            # after the payload, however many fake ones it contains) -- this
            # is a property of wrap_untrusted()'s construction, not of the
            # payload content.
            open_idx = prompt.index("<<UNTRUSTED CONTENT")
            close_idx = prompt.rindex("<<END UNTRUSTED CONTENT>>")
            payload_idx = prompt.index(payload.text)
            assert open_idx < payload_idx < close_idx  # ...but strictly inside the delimiters
            assert system_instruction == UNTRUSTED_CONTENT_WARNING


class TestEdgeProposalDelimitsUntrustedText:
    def test_concept_listing_is_wrapped(self):
        gen = FakeGenerationGateway().set_default('{"edges": []}')
        concepts = [
            ConceptForEdges(id=uuid.uuid4(), name="A", definition=INJECTION_PAYLOADS[0].text),
            ConceptForEdges(id=uuid.uuid4(), name="B", definition="Normal definition."),
        ]
        propose_edges(concepts, gen)

        prompt = gen.calls[0]
        assert "<<UNTRUSTED CONTENT" in prompt and "<<END UNTRUSTED CONTENT>>" in prompt
        open_idx = prompt.index("<<UNTRUSTED CONTENT")
        close_idx = prompt.index("<<END UNTRUSTED CONTENT>>")
        payload_idx = prompt.index(INJECTION_PAYLOADS[0].text)
        assert open_idx < payload_idx < close_idx
        assert gen.system_instructions[0] == UNTRUSTED_CONTENT_WARNING


class TestAdjudicationDelimitsUntrustedText:
    def test_concept_pair_is_wrapped(self):
        gen = FakeGenerationGateway().set_default('{"same_concept": false, "merged_definition": null}')
        a = CandidateConcept(name="A", definition=INJECTION_PAYLOADS[1].text, source_chunk_ids=[], embedding=[])
        b = NormalizedConcept(canonical_key="b", name="B", definition="Normal.")
        _adjudicate(a, b, gen)

        prompt = gen.calls[0]
        assert "<<UNTRUSTED CONTENT" in prompt and "<<END UNTRUSTED CONTENT>>" in prompt
        assert gen.system_instructions[0] == UNTRUSTED_CONTENT_WARNING


class TestTutorDelimitsUntrustedTextAndSeparatesChannels:
    def test_retrieved_chunk_text_never_shares_a_channel_with_instructions(self):
        for payload in INJECTION_PAYLOADS:
            chunks = [ReferenceChunk(chunk_id="c1", text=payload.text)]
            prompt = build_tutor_prompt("What is X?", chunks)
            # The payload is quoted (inert reference material)...
            assert payload.text in prompt
            # ...strictly inside the tutor's own delimiters...
            open_idx = prompt.index("<<REFERENCE MATERIAL")
            close_idx = prompt.index("<<END REFERENCE MATERIAL>>")
            payload_idx = prompt.index(payload.text)
            assert open_idx < payload_idx < close_idx
            # ...and never appears in the separate system_instruction channel.
            assert payload.text not in SYSTEM_INSTRUCTION
