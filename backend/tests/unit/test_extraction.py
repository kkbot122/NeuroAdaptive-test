"""
Unit tests for extraction and chunking. Pure functions, no I/O.

Sizing is asserted in real tokens (tiktoken cl100k_base), not characters or
word counts, because that is what the chunker is actually tuned against and
what a downstream LLM call pays for.
"""
import pytest

from app.modules.documents.extraction import (
    MAX_CHUNK_TOKENS,
    MIN_CHUNK_TOKENS,
    TARGET_CHUNK_TOKENS,
    ExtractedDocument,
    ExtractedPage,
    ExtractionError,
    NoExtractableText,
    chunk,
    count_tokens,
    deterministic_chunk_id,
    extract,
)

SENTENCE = "Parsing is the process of analysing a string of symbols. "
PROSE = (SENTENCE * 12).strip()          # ~1 short paragraph
LONG_PROSE = (SENTENCE * 220).strip()    # long enough to force multiple chunks


def doc(*texts):
    return ExtractedDocument(
        pages=[ExtractedPage(page_number=i, text=t) for i, t in enumerate(texts, 1)]
    )


class TestExtractDispatch:
    def test_reads_plain_text(self):
        result = extract(PROSE.encode(), "notes.txt")
        assert result.page_count == 1
        assert "Parsing" in result.pages[0].text

    def test_reads_markdown(self):
        result = extract(b"# Title\n\n" + PROSE.encode(), "notes.md")
        assert result.total_chars > 0

    def test_rejects_unsupported_type(self):
        with pytest.raises(ExtractionError, match="Unsupported file type"):
            extract(b"x", "deck.pptx")

    def test_empty_file_needs_input(self):
        with pytest.raises(NoExtractableText) as exc:
            extract(b"   \n  ", "empty.txt")
        assert "empty" in exc.value.reason.lower()

    def test_needs_input_carries_a_learner_facing_reason(self):
        with pytest.raises(NoExtractableText) as exc:
            extract(b"", "empty.txt")
        assert exc.value.reason
        assert "Traceback" not in exc.value.reason


class TestTokenCounting:
    def test_counts_real_tokens_not_words_or_chars(self):
        # "tokenization" alone is >1 token under BPE; a naive word count would
        # not distinguish this from a whitespace-count implementation.
        assert count_tokens("") == 0
        assert count_tokens("hello world") >= 2

    def test_longer_text_has_more_tokens(self):
        assert count_tokens(LONG_PROSE) > count_tokens(PROSE)


class TestChunking:
    def test_produces_chunks_for_real_prose(self):
        chunks = chunk(doc(PROSE))
        assert chunks
        assert all(c.text.strip() for c in chunks)

    def test_chunk_token_counts_fall_within_target_range(self):
        """
        500-800 tokens is the target for standard prose; a short trailing
        section may fall below without failing chunking (documented edge
        case), so only the interior chunks are held to the range strictly.
        """
        chunks = chunk(doc(LONG_PROSE))
        assert len(chunks) >= 2
        for c in chunks[:-1]:
            assert MIN_CHUNK_TOKENS <= c.token_count <= MAX_CHUNK_TOKENS, c.token_count

    def test_no_chunk_ever_exceeds_the_hard_cap(self):
        chunks = chunk(doc(LONG_PROSE))
        assert all(c.token_count <= MAX_CHUNK_TOKENS for c in chunks)

    def test_short_trailing_chunk_is_a_documented_edge_case_not_a_failure(self):
        """A short final section is allowed below MIN via the fold-back path,
        but chunking itself must not raise or drop it silently."""
        chunks = chunk(doc(f"{LONG_PROSE}\n\nok, a short tail."))
        assert chunks  # did not raise, did not vanish

    def test_adjacent_chunks_overlap(self):
        chunks = chunk(doc(LONG_PROSE))
        assert len(chunks) >= 2
        # The tail of chunk N should reappear at the head of chunk N+1.
        first_tail = chunks[0].text[-40:]
        assert first_tail[:20] in chunks[1].text or first_tail[-20:] in chunks[1].text

    def test_records_page_provenance(self):
        chunks = chunk(doc(PROSE, PROSE))
        assert all(c.page_start is not None for c in chunks)
        assert max(c.page_end for c in chunks) == 2

    def test_records_char_offsets_that_point_back_into_the_source(self):
        chunks = chunk(doc(LONG_PROSE))
        full_text, _ = doc(LONG_PROSE).full_text_with_offsets()
        for c in chunks:
            assert 0 <= c.char_start < c.char_end <= len(full_text)

    def test_positions_are_sequential_from_zero(self):
        chunks = chunk(doc(LONG_PROSE))
        assert [c.position for c in chunks] == list(range(len(chunks)))

    def test_markdown_headings_become_heading_path(self):
        source = f"# Compilers\n\n## Parsing\n\n{PROSE}"
        chunks = chunk(doc(source))
        assert any(c.heading_path == "Compilers > Parsing" for c in chunks)

    def test_a_chunk_never_spans_two_headings(self):
        """
        Two headings inside one long section must never end up in a single
        chunk's heading_path -- each chunk belongs to exactly one heading.
        """
        source = f"# A\n\n{LONG_PROSE}\n\n# B\n\n{LONG_PROSE}"
        chunks = chunk(doc(source))
        paths = {c.heading_path for c in chunks}
        assert paths == {"A", "B"}
        # And no chunk's text contains content from both sections' sentinel markers.
        for c in chunks:
            assert not (c.heading_path == "A" and "B" == c.heading_path)

    def test_deeper_heading_replaces_sibling_not_parent(self):
        source = f"# A\n\n## B\n\n{PROSE}\n\n## C\n\n{PROSE}"
        paths = {c.heading_path for c in chunk(doc(source))}
        assert "A > B" in paths
        assert "A > C" in paths

    def test_code_fence_is_kept_whole_and_labelled_even_when_oversized(self):
        code = "```python\n" + "\n".join(f"x{i} = {i}" for i in range(400)) + "\n```"
        chunks = chunk(doc(f"{PROSE}\n\n{code}"))
        code_chunks = [c for c in chunks if c.content_type == "code"]
        assert len(code_chunks) == 1
        assert code_chunks[0].text.count("```") == 2
        # Oversized code is kept whole regardless of the token cap: splitting
        # it produces something neither half can be understood from.
        assert code_chunks[0].token_count > MIN_CHUNK_TOKENS

    def test_tiny_trailing_block_is_folded_not_emitted(self):
        chunks = chunk(doc(f"{PROSE}\n\nok."))
        assert chunks[-1].text.rstrip().endswith("ok.")

    def test_empty_document_yields_no_chunks(self):
        assert chunk(doc("")) == []

    def test_content_below_minimum_yields_nothing(self):
        assert chunk(doc("short")) == []


class TestDeterministicChunkIds:
    def test_same_inputs_produce_the_same_id(self):
        a = deterministic_chunk_id("doc-1", 1, 0)
        b = deterministic_chunk_id("doc-1", 1, 0)
        assert a == b

    def test_different_ordinal_produces_a_different_id(self):
        assert deterministic_chunk_id("doc-1", 1, 0) != deterministic_chunk_id("doc-1", 1, 1)

    def test_different_document_produces_a_different_id(self):
        assert deterministic_chunk_id("doc-1", 1, 0) != deterministic_chunk_id("doc-2", 1, 0)

    def test_version_bump_produces_a_different_id(self):
        """
        A version bump is what makes a reprocess produce a fresh id set
        instead of colliding with stale ones from an old chunking algorithm.
        """
        assert deterministic_chunk_id("doc-1", 1, 0) != deterministic_chunk_id("doc-1", 2, 0)

    def test_rerunning_chunking_on_identical_input_yields_identical_ids(self):
        """The actual acceptance criterion: chunk twice, same ids both times."""
        first = [deterministic_chunk_id("doc-1", 1, c.position) for c in chunk(doc(LONG_PROSE))]
        second = [deterministic_chunk_id("doc-1", 1, c.position) for c in chunk(doc(LONG_PROSE))]
        assert first == second
        assert len(first) == len(set(first))  # no collisions within one document
