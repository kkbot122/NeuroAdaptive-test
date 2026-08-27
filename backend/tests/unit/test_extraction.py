"""Unit tests for extraction and chunking. Pure functions, no I/O."""
import pytest

from app.modules.documents.extraction import (
    MIN_CHUNK_CHARS,
    ExtractedDocument,
    ExtractedPage,
    ExtractionError,
    NoExtractableText,
    chunk,
    extract,
)

PROSE = ("Parsing is the process of analysing a string of symbols. " * 12).strip()


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
        """The reason is shown to the learner, so it must not be a stack trace."""
        with pytest.raises(NoExtractableText) as exc:
            extract(b"", "empty.txt")
        assert exc.value.reason
        assert "Traceback" not in exc.value.reason


class TestChunking:
    def test_produces_chunks_for_real_prose(self):
        chunks = chunk(doc(PROSE))
        assert chunks
        assert all(c.text.strip() for c in chunks)

    def test_records_page_provenance(self):
        chunks = chunk(doc(PROSE, PROSE))
        assert all(c.page_start is not None for c in chunks)
        assert max(c.page_end for c in chunks) == 2

    def test_positions_are_sequential_from_zero(self):
        chunks = chunk(doc(PROSE * 3))
        assert [c.position for c in chunks] == list(range(len(chunks)))

    def test_markdown_headings_become_heading_path(self):
        source = f"# Compilers\n\n## Parsing\n\n{PROSE}"
        chunks = chunk(doc(source))
        assert any(c.heading_path == "Compilers > Parsing" for c in chunks)

    def test_deeper_heading_replaces_sibling_not_parent(self):
        source = f"# A\n\n## B\n\n{PROSE}\n\n## C\n\n{PROSE}"
        paths = {c.heading_path for c in chunk(doc(source))}
        assert "A > B" in paths
        assert "A > C" in paths

    def test_code_fence_is_kept_whole_and_labelled(self):
        code = "```python\n" + "\n".join(f"x{i} = {i}" for i in range(40)) + "\n```"
        chunks = chunk(doc(f"{PROSE}\n\n{code}"))
        code_chunks = [c for c in chunks if c.content_type == "code"]
        assert len(code_chunks) == 1
        assert code_chunks[0].text.count("```") == 2

    def test_tiny_trailing_block_is_folded_not_emitted(self):
        chunks = chunk(doc(f"{PROSE}\n\nok."))
        assert all(len(c.text) >= MIN_CHUNK_CHARS for c in chunks)
        assert chunks[-1].text.rstrip().endswith("ok.")

    def test_empty_document_yields_no_chunks(self):
        assert chunk(doc("")) == []

    def test_content_below_minimum_yields_nothing(self):
        assert chunk(doc("short")) == []
