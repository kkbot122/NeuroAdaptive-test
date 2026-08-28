"""
Unit tests for magic-byte sniffing. Pure function, no I/O.

The concrete threat: a file renamed from .exe to .pdf must be rejected
because its actual bytes are not a PDF, not accepted because the extension
looked right.
"""
import pytest

from app.modules.documents.magic_bytes import SignatureMismatch, verify_signature


class TestDisguisedBinaries:
    def test_exe_renamed_to_pdf_is_rejected(self):
        exe_bytes = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff"
        with pytest.raises(SignatureMismatch, match="executable"):
            verify_signature("totally-a-pdf.pdf", exe_bytes)

    def test_elf_renamed_to_pdf_is_rejected(self):
        elf_bytes = b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 20
        with pytest.raises(SignatureMismatch, match="executable"):
            verify_signature("notes.pdf", elf_bytes)

    def test_zip_renamed_to_txt_is_rejected(self):
        """docx/pptx/xlsx and a zipped exe all share this container signature."""
        zip_bytes = b"PK\x03\x04" + b"\x00" * 20
        with pytest.raises(SignatureMismatch, match="zip archive"):
            verify_signature("notes.txt", zip_bytes)

    def test_png_renamed_to_md_is_rejected(self):
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
        with pytest.raises(SignatureMismatch):
            verify_signature("readme.md", png_bytes)

    def test_disguised_binary_is_rejected_regardless_of_claimed_extension(self):
        """The check runs before the extension is even consulted for this case."""
        exe_bytes = b"MZ" + b"\x00" * 30
        for ext in ("pdf", "txt", "md"):
            with pytest.raises(SignatureMismatch):
                verify_signature(f"file.{ext}", exe_bytes)


class TestPdfHeaderCheck:
    def test_genuine_pdf_header_is_accepted(self):
        verify_signature("real.pdf", b"%PDF-1.7\n%..." + b"\x00" * 20)  # must not raise

    def test_pdf_extension_without_pdf_header_is_rejected(self):
        with pytest.raises(SignatureMismatch, match="PDF header"):
            verify_signature("fake.pdf", b"this is just plain text, not a pdf at all")

    def test_pdf_check_does_not_apply_to_txt_files(self):
        verify_signature("notes.txt", b"plain text content")  # must not raise


class TestBenignContent:
    def test_ordinary_text_is_accepted(self):
        verify_signature("notes.txt", "Parsing is the process of analysis.".encode())

    def test_ordinary_markdown_is_accepted(self):
        verify_signature("notes.md", b"# Heading\n\nSome prose.")

    def test_empty_content_does_not_crash(self):
        # Emptiness itself is rejected elsewhere (upload validation); this
        # function only checks signatures and must not raise for the wrong
        # reason on an edge case it does not own.
        verify_signature("notes.txt", b"")
