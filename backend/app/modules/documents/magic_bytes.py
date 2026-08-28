"""
Magic-byte sniffing for the three formats this sprint supports.

A dependency-free signature check rather than python-magic/libmagic: the
supported format set is exactly {pdf, txt, md}, which a handful of known
signatures cover completely, and libmagic needs an OS package (Dockerfile
change for the container, a different install for local dev, another again
for CI) for a check this narrow can do without any of it.

The concrete threat this closes: a file renamed from .exe to .pdf must be
rejected because its actual bytes are not a PDF, not accepted because the
extension looked right.
"""
from pathlib import Path

PDF_SIGNATURE = b"%PDF-"

# Byte signatures of formats that must never be accepted regardless of what
# extension they were renamed to.
_KNOWN_BINARY_SIGNATURES = (
    (b"MZ", "a Windows executable"),
    (b"\x7fELF", "a Linux executable"),
    (b"\xca\xfe\xba\xbe", "a Mach-O/Java class executable"),
    (b"PK\x03\x04", "a zip archive (docx/pptx/xlsx/exe-in-zip all use this container)"),
    (b"\x89PNG\r\n\x1a\n", "a PNG image"),
    (b"\xff\xd8\xff", "a JPEG image"),
    (b"GIF8", "a GIF image"),
    (b"Rar!\x1a\x07", "a RAR archive"),
    (b"\x1f\x8b", "a gzip archive"),
)


class SignatureMismatch(Exception):
    """The file's actual bytes do not match what its extension claims."""


def verify_signature(filename: str, content: bytes) -> None:
    """
    Raise SignatureMismatch if `content` is not genuinely the format its
    extension claims. Silently accept anything not in the checked set (the
    extension allowlist in documents/service.py is what actually restricts
    supported types; this only catches disguised binaries within it).
    """
    suffix = Path(filename or "").suffix.lower()

    for signature, description in _KNOWN_BINARY_SIGNATURES:
        if content.startswith(signature):
            raise SignatureMismatch(
                f"This file's contents are {description}, not a document. "
                "Renaming a file's extension does not change what it is."
            )

    if suffix == ".pdf" and not content.startswith(PDF_SIGNATURE):
        raise SignatureMismatch(
            "This file does not start with a valid PDF header. It may have "
            "been renamed from a different file type."
        )
