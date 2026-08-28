"""
T2 (malicious/malformed uploads): page-count-bomb protection and
encrypted-PDF detection, exercised against real PDF bytes built with pypdf
(already a project dependency) rather than hand-authored binary fixtures.

Magic-byte/extension rejection (mandate test 3) is already covered by
tests/unit/test_magic_bytes.py -- not duplicated here.
"""
import io
from typing import Optional

import pytest
from pypdf import PdfWriter

from app.modules.documents.extraction import MAX_PDF_PAGES, NoExtractableText, extract


def make_pdf_bytes(num_pages: int = 1, encrypt_with: Optional[str] = None) -> bytes:
    writer = PdfWriter()
    for _ in range(num_pages):
        writer.add_blank_page(width=200, height=200)
    if encrypt_with:
        writer.encrypt(encrypt_with)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


class TestPageCountBomb:
    def test_a_pdf_over_the_page_limit_is_rejected(self):
        raw = make_pdf_bytes(num_pages=MAX_PDF_PAGES + 1)
        with pytest.raises(NoExtractableText) as exc_info:
            extract(raw, "huge.pdf")
        assert "page limit" in str(exc_info.value)
        assert str(MAX_PDF_PAGES) in str(exc_info.value)

    def test_a_pdf_at_the_limit_is_not_rejected_for_page_count(self):
        # Blank pages have no extractable text, so this still raises
        # NoExtractableText -- but for the "no selectable text" reason, not
        # the page-count reason, proving the limit itself is inclusive.
        raw = make_pdf_bytes(num_pages=MAX_PDF_PAGES)
        with pytest.raises(NoExtractableText) as exc_info:
            extract(raw, "at_limit.pdf")
        assert "page limit" not in str(exc_info.value)


class TestEncryptedPdf:
    def test_a_password_protected_pdf_gets_a_specific_clear_message(self):
        raw = make_pdf_bytes(num_pages=1, encrypt_with="secret123")
        with pytest.raises(NoExtractableText) as exc_info:
            extract(raw, "protected.pdf")
        message = str(exc_info.value)
        assert "password-protected" in message
        # Specific to encryption, not a generic failure -- distinguishable
        # from the page-count and no-text-found messages.
        assert "page limit" not in message
        assert "scan" not in message
