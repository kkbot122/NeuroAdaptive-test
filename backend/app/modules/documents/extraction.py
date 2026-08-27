"""
Document text extraction and structure-aware chunking.

Pure functions over bytes and text — no database, no network, no model call —
so both halves are testable without infrastructure. The job runner is what
persists their output.

Scope this sprint (frozen substitutions): native-text PDF, TXT and Markdown.
No OCR, no multimodal fallback, no DOCX/PPTX. A PDF whose pages yield no
extractable text is an explicit NEEDS_INPUT outcome with a plain-language
reason, never a silent empty result.
"""
import io
import re
from dataclasses import dataclass, field
from typing import List, Optional

# Unvalidated defaults. Named and configurable per AGENTS.md §1; these are
# starting points chosen for readability of retrieved context, not tuned.
TARGET_CHUNK_CHARS = 1200
MIN_CHUNK_CHARS = 120
CHUNK_OVERLAP_CHARS = 150

SUPPORTED_TEXT_SUFFIXES = (".txt", ".md", ".markdown")


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


@dataclass
class ProposedChunk:
    position: int
    text: str
    heading_path: Optional[str]
    content_type: str
    page_start: Optional[int]
    page_end: Optional[int]


# ── extraction ───────────────────────────────────────────────────────────────


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
    # Plain text has no pages; treat the whole file as page 1.
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

    if document.total_chars < MIN_CHUNK_CHARS:
        raise NoExtractableText(
            "No selectable text was found in this PDF — it looks like a scan or "
            "images of pages. Scanned documents are not supported yet; upload a "
            "text-based PDF, or paste the content as a .txt or .md file."
        )

    return document


# ── chunking ─────────────────────────────────────────────────────────────────

_MD_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
# A short, title-case-ish line with no terminal punctuation, used as a weak
# heading signal in extracted PDF text which carries no markup.
_BARE_HEADING = re.compile(r"^(?:\d+(?:\.\d+)*\.?\s+)?[A-Z][^.!?]{2,79}$")

_FENCE = re.compile(r"^```")


def chunk(document: ExtractedDocument) -> List[ProposedChunk]:
    """
    Split into retrieval-sized pieces, preserving heading context and pages.

    Paragraph boundaries are respected before size: a chunk that splits
    mid-sentence retrieves badly and cites worse. Fenced code blocks are never
    split, and are marked so retrieval can tell prose from code.
    """
    chunks: List[ProposedChunk] = []
    heading_stack: List[str] = []
    buffer: List[str] = []
    buffer_pages: List[int] = []
    position = 0

    def heading_path() -> Optional[str]:
        return " > ".join(heading_stack) if heading_stack else None

    def flush(content_type: str = "prose", force: bool = False) -> None:
        nonlocal buffer, buffer_pages, position
        text = "\n\n".join(b for b in buffer if b.strip()).strip()
        buffer, pages = [], buffer_pages
        buffer_pages = []
        if not force and len(text) < MIN_CHUNK_CHARS:
            # Too small to stand alone; fold it back rather than emit a stub.
            if text and chunks:
                chunks[-1].text = f"{chunks[-1].text}\n\n{text}"
                chunks[-1].page_end = max(pages) if pages else chunks[-1].page_end
            return
        chunks.append(
            ProposedChunk(
                position=position,
                text=text,
                heading_path=heading_path(),
                content_type=content_type,
                page_start=min(pages) if pages else None,
                page_end=max(pages) if pages else None,
            )
        )
        position += 1

    for page in document.pages:
        in_fence = False
        fence_buffer: List[str] = []

        for block in _split_blocks(page.text):
            stripped = block.strip()
            if not stripped:
                continue

            if _FENCE.match(stripped):
                if in_fence:
                    # Closing fence of a block that spanned blank lines.
                    fence_buffer.append(block)
                    flush()  # close any prose that preceded the code
                    buffer = ["\n".join(fence_buffer)]
                    buffer_pages = [page.page_number]
                    flush(content_type="code", force=True)
                    fence_buffer, in_fence = [], False
                elif stripped.count("```") >= 2:
                    # A complete fenced block with no blank lines inside, so
                    # the opening and closing fences arrive in one block.
                    flush()
                    buffer = [stripped]
                    buffer_pages = [page.page_number]
                    flush(content_type="code", force=True)
                else:
                    in_fence = True
                    fence_buffer = [block]
                continue

            if in_fence:
                fence_buffer.append(block)
                continue

            md = _MD_HEADING.match(stripped)
            if md or _looks_like_bare_heading(stripped):
                flush()
                level = len(md.group(1)) if md else 1
                title = md.group(2) if md else stripped
                del heading_stack[level - 1 :]
                heading_stack.append(title)
                continue

            buffer.append(stripped)
            buffer_pages.append(page.page_number)

            if sum(len(b) for b in buffer) >= TARGET_CHUNK_CHARS:
                tail = buffer[-1][-CHUNK_OVERLAP_CHARS:] if buffer else ""
                last_page = buffer_pages[-1] if buffer_pages else page.page_number
                flush()
                # Carry a little context forward so a boundary does not sever
                # a definition from its explanation.
                if tail.strip():
                    buffer = [tail.strip()]
                    buffer_pages = [last_page]

        if in_fence and fence_buffer:
            buffer.extend(fence_buffer)
            buffer_pages.append(page.page_number)

    flush()
    return chunks


def _split_blocks(text: str) -> List[str]:
    """Blank-line separated blocks, with single newlines preserved inside."""
    return re.split(r"\n\s*\n", text or "")


def _looks_like_bare_heading(line: str) -> bool:
    if "\n" in line or len(line) > 80:
        return False
    return bool(_BARE_HEADING.match(line))
