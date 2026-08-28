"""
Document text extraction and structure-aware chunking.

Pure functions over bytes and text -- no database, no network, no model call --
so both halves are testable without infrastructure. The job runner is what
persists their output.

Scope this sprint (frozen substitutions): native-text PDF, TXT and Markdown.
No OCR, no multimodal fallback, no DOCX/PPTX. A PDF whose pages yield no
extractable text is an explicit NEEDS_INPUT outcome with a plain-language
reason, never a silent empty result.
"""
import io
import re
import uuid
from dataclasses import dataclass, field
from typing import List, Optional

import tiktoken

# Unvalidated defaults. Named and configurable per AGENTS.md §1. Sized in
# tokens, not characters, because token budget is what a downstream LLM call
# actually pays for.
TARGET_CHUNK_TOKENS = 650      # midpoint of the 500-800 token target range
MIN_CHUNK_TOKENS = 40
MAX_CHUNK_TOKENS = 800
CHUNK_OVERLAP_TOKENS = 75      # midpoint of the 50-100 token overlap range

SUPPORTED_TEXT_SUFFIXES = (".txt", ".md", ".markdown")

# T2 (Phase 6, page-count-bomb protection): named, unvalidated default.
MAX_PDF_PAGES = 500

# Bumped whenever extraction or chunking logic changes in a way that would
# alter chunk boundaries or content for the same input. Deterministic chunk
# IDs are derived from this, so a version bump is what makes a reprocess
# produce a fresh set of IDs instead of silently colliding with stale ones.
EXTRACTION_VERSION = 1

# A stable namespace for chunk id derivation (uuid5), so ids are reproducible
# across processes without depending on Python's hash randomization.
_CHUNK_ID_NAMESPACE = uuid.UUID("6f2f9a7e-6b1a-4a3e-9c2d-8f1e2b7a9c3d")

_encoding = tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    return len(_encoding.encode(text))


def deterministic_chunk_id(document_id, extraction_version: int, ordinal: int) -> uuid.UUID:
    """
    hash(document_id, extraction_version, ordinal) -> a stable UUID.

    Makes a retried job overwrite identically instead of duplicating, and
    means a citation recorded elsewhere keeps pointing at the same chunk
    across a reprocess with the same extraction_version.
    """
    key = f"{document_id}:{extraction_version}:{ordinal}"
    return uuid.uuid5(_CHUNK_ID_NAMESPACE, key)


class ExtractionError(Exception):
    """Raised when a document cannot be read at all."""


class NoExtractableText(Exception):
    """
    Raised when a document parses but yields no usable native text.

    Carries a learner-facing reason. This is the NEEDS_INPUT path: a scanned
    or image-only PDF is a legitimate document that this sprint cannot read,
    and saying so is required rather than emitting empty output.
    """

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass
class ExtractedPage:
    page_number: int
    text: str


@dataclass
class ExtractedDocument:
    pages: List[ExtractedPage] = field(default_factory=list)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def total_chars(self) -> int:
        return sum(len(p.text) for p in self.pages)

    def full_text_with_offsets(self):
        """
        Concatenate all pages into one string, joined by a blank line, and
        return (text, [(page_number, start_offset, end_offset), ...]) so a
        later char offset within the concatenation maps back to a page.
        """
        parts = []
        spans = []
        cursor = 0
        for page in self.pages:
            start = cursor
            parts.append(page.text)
            cursor += len(page.text)
            spans.append((page.page_number, start, cursor))
            cursor += 2  # for the "\n\n" joiner
            parts.append("\n\n")
        return "".join(parts), spans


@dataclass
class ProposedChunk:
    position: int
    text: str
    heading_path: Optional[str]
    content_type: str
    page_start: Optional[int]
    page_end: Optional[int]
    char_start: int
    char_end: int
    token_count: int


# -- extraction ----------------------------------------------------------------


def extract(raw: bytes, filename: str) -> ExtractedDocument:
    """Dispatch on file suffix. Extension-only checking is a documented P0 limit."""
    lowered = (filename or "").lower()
    if lowered.endswith(".pdf"):
        return _extract_pdf(raw)
    if lowered.endswith(SUPPORTED_TEXT_SUFFIXES):
        return _extract_plaintext(raw)
    raise ExtractionError(f"Unsupported file type: {filename}")


def _extract_plaintext(raw: bytes) -> ExtractedDocument:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = raw.decode("latin-1")
        except Exception as exc:  # pragma: no cover - defensive
            raise ExtractionError("File is not readable as text") from exc

    if not text.strip():
        raise NoExtractableText("The file is empty.")
    return ExtractedDocument(pages=[ExtractedPage(page_number=1, text=text)])


def _extract_pdf(raw: bytes) -> ExtractedDocument:
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(raw))
    except Exception as exc:
        raise ExtractionError("The PDF could not be opened. It may be corrupt.") from exc

    if reader.is_encrypted:
        raise NoExtractableText(
            "This PDF is password-protected. Upload an unprotected copy."
        )

    # T2 (Phase 6): a page-count check, cheap because it walks pypdf's page
    # tree without calling extract_text() per page -- bounds worst-case
    # processing time/memory against a page-count-bomb PDF (a small file
    # engineered to declare an extreme page count) before any real
    # per-page work starts. Named, unvalidated default.
    page_count = len(reader.pages)
    if page_count > MAX_PDF_PAGES:
        raise NoExtractableText(
            f"This PDF has {page_count} pages, over the {MAX_PDF_PAGES}-page limit. "
            "Split it into smaller files and upload them separately."
        )

    pages = []
    for index, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        pages.append(ExtractedPage(page_number=index, text=text))

    document = ExtractedDocument(pages=pages)

    if document.page_count == 0:
        raise NoExtractableText("The PDF contains no pages.")

    if document.total_chars < 120:
        raise NoExtractableText(
            "No selectable text was found in this PDF -- it looks like a scan or "
            "images of pages. Scanned documents are not supported yet; upload a "
            "text-based PDF, or paste the content as a .txt or .md file."
        )

    return document


# -- chunking --------------------------------------------------------------------

_MD_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_BARE_HEADING = re.compile(r"^(?:\d+(?:\.\d+)*\.?\s+)?[A-Z][^.!?]{2,79}$")
_FENCE = re.compile(r"^```")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _split_oversized_block(block: str, limit: int) -> List[str]:
    """
    A single paragraph with no internal blank lines can still exceed the
    token cap on its own (long-form prose with no natural break). Split at
    sentence boundaries and accumulate up to `limit` tokens per piece, so the
    cap is respected without cutting a sentence in half.
    """
    sentences = [s for s in _SENTENCE_SPLIT.split(block) if s.strip()]
    if len(sentences) <= 1:
        return [block]

    pieces: List[str] = []
    current: List[str] = []
    current_tokens = 0
    for sentence in sentences:
        sentence_tokens = count_tokens(sentence)
        if current and current_tokens + sentence_tokens > limit:
            pieces.append(" ".join(current))
            current, current_tokens = [], 0
        current.append(sentence)
        current_tokens += sentence_tokens
    if current:
        pieces.append(" ".join(current))
    return pieces


def chunk(document: ExtractedDocument) -> List[ProposedChunk]:
    """
    Split into retrieval-sized pieces, preserving heading context, pages, and
    character offsets into the full document text.

    A chunk never spans a heading boundary. Code fences are kept whole and
    tagged content_type="code" regardless of size, since splitting a code
    block produces something neither half can be understood from. Sizing and
    overlap are measured in tokens (tiktoken cl100k_base), because token
    budget -- not character count -- is what a downstream LLM call pays for.
    """
    full_text, page_spans = document.full_text_with_offsets()

    chunks: List[ProposedChunk] = []
    heading_stack: List[str] = []
    buffer: List[str] = []
    buffer_pages: List[int] = []
    buffer_offsets: List[int] = []
    position = 0

    def heading_path() -> Optional[str]:
        return " > ".join(heading_stack) if heading_stack else None

    def flush(content_type: str = "prose", force: bool = False) -> None:
        nonlocal buffer, buffer_pages, buffer_offsets, position
        text = "\n\n".join(b for b in buffer if b.strip()).strip()
        pages = buffer_pages
        offsets = buffer_offsets
        buffer, buffer_pages, buffer_offsets = [], [], []

        if not text:
            return

        tokens = count_tokens(text)
        if not force and tokens < MIN_CHUNK_TOKENS:
            if chunks:
                prev = chunks[-1]
                prev.text = f"{prev.text}\n\n{text}"
                prev.page_end = max(pages) if pages else prev.page_end
                if offsets:
                    prev.char_end = offsets[-1] + len(text)
                prev.token_count = count_tokens(prev.text)
            return

        char_start = offsets[0] if offsets else 0
        char_end = char_start + len(text)
        chunks.append(
            ProposedChunk(
                position=position,
                text=text,
                heading_path=heading_path(),
                content_type=content_type,
                page_start=min(pages) if pages else None,
                page_end=max(pages) if pages else None,
                char_start=char_start,
                char_end=char_end,
                token_count=tokens,
            )
        )
        position += 1

    for page in document.pages:
        page_text = page.text
        try:
            page_start_offset = full_text.index(page_text) if page_text.strip() else 0
        except ValueError:
            page_start_offset = 0

        in_fence = False
        fence_buffer: List[str] = []
        fence_offset = None
        search_from = 0

        for block in _split_blocks(page_text):
            if block:
                found = page_text.find(block, search_from)
                block_offset = page_start_offset + (found if found >= 0 else search_from)
                search_from = (found if found >= 0 else search_from) + len(block)
            else:
                block_offset = page_start_offset + search_from

            stripped = block.strip()
            if not stripped:
                continue

            if _FENCE.match(stripped):
                if in_fence:
                    fence_buffer.append(block)
                    flush()
                    buffer = ["\n".join(fence_buffer)]
                    buffer_pages = [page.page_number]
                    buffer_offsets = [fence_offset if fence_offset is not None else block_offset]
                    flush(content_type="code", force=True)
                    fence_buffer, in_fence = [], False
                elif stripped.count("```") >= 2:
                    flush()
                    buffer = [stripped]
                    buffer_pages = [page.page_number]
                    buffer_offsets = [block_offset]
                    flush(content_type="code", force=True)
                else:
                    in_fence = True
                    fence_buffer = [block]
                    fence_offset = block_offset
                continue

            if in_fence:
                fence_buffer.append(block)
                continue

            md = _MD_HEADING.match(stripped)
            if md or _looks_like_bare_heading(stripped):
                flush()
                level = len(md.group(1)) if md else 1
                title = md.group(2) if md else stripped
                del heading_stack[level - 1:]
                heading_stack.append(title)
                continue

            # A single block (one paragraph, no internal blank line) can
            # itself exceed the target -- long-form prose with no natural
            # break. Pre-split it at sentence boundaries so no piece we add
            # to the buffer can alone blow through the hard cap.
            sub_pieces = (
                _split_oversized_block(stripped, TARGET_CHUNK_TOKENS)
                if count_tokens(stripped) > TARGET_CHUNK_TOKENS
                else [stripped]
            )

            multi_piece = len(sub_pieces) > 1
            for piece_index, piece in enumerate(sub_pieces):
                buffer.append(piece)
                buffer_pages.append(page.page_number)
                buffer_offsets.append(block_offset)

                current_tokens = sum(count_tokens(b) for b in buffer)
                # A piece produced by splitting an oversized block was already
                # sized to be a complete chunk on its own (up to
                # TARGET_CHUNK_TOKENS). Flush right after it rather than
                # waiting to reach the target again -- otherwise two
                # already-target-sized pieces accumulate before the
                # threshold trips, roughly doubling the chunk size.
                is_full_sized_piece = multi_piece and piece_index < len(sub_pieces) - 1
                if current_tokens >= TARGET_CHUNK_TOKENS or is_full_sized_piece:
                    tail_text = buffer[-1]
                    tail_page = buffer_pages[-1]
                    flush()
                    tail_tokens = _encoding.encode(tail_text)
                    if len(tail_tokens) > CHUNK_OVERLAP_TOKENS:
                        overlap_text = _encoding.decode(tail_tokens[-CHUNK_OVERLAP_TOKENS:])
                    else:
                        overlap_text = tail_text
                    if overlap_text.strip():
                        buffer = [overlap_text.strip()]
                        buffer_pages = [tail_page]
                        buffer_offsets = [block_offset]

        if in_fence and fence_buffer:
            buffer.extend(fence_buffer)
            buffer_pages.append(page.page_number)
            buffer_offsets.append(fence_offset if fence_offset is not None else page_start_offset)

    flush()
    return chunks


def _split_blocks(text: str) -> List[str]:
    return re.split(r"\n\s*\n", text or "")


def _looks_like_bare_heading(line: str) -> bool:
    if "\n" in line or len(line) > 80:
        return False
    return bool(_BARE_HEADING.match(line))
