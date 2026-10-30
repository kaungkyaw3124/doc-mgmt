"""
Security regression tests for Priority 5 — Secure Upload Content-Type.

These test the pure decision logic (app.core.upload_safety) that
products.py's upload/download endpoints now use instead of trusting the
client-supplied Content-Type. No DB/MinIO needed — these are pure
filename-in, decision-out functions.
"""

import pytest

from app.core.upload_safety import (
    safe_content_type,
    should_force_download,
    content_matches_extension,
    detect_content_kind,
)


def test_normal_pdf_gets_correct_mime_and_is_not_forced():
    assert safe_content_type("invoice.pdf") == "application/pdf"
    assert should_force_download("invoice.pdf") is False


def test_normal_image_gets_correct_mime_and_is_not_forced():
    assert safe_content_type("photo.jpg") == "image/jpeg"
    assert safe_content_type("photo.PNG") == "image/png"
    assert should_force_download("photo.jpg") is False


@pytest.mark.parametrize("filename", ["malware.html", "page.htm", "evil.xhtml", "script.js"])
def test_html_or_script_uploaded_directly_is_neutralized(filename):
    """An attacker uploading real HTML/JS with its real extension: not a
    recognized safe type (so stored/served as an opaque blob) AND always
    forced to download, never opened inline."""
    assert safe_content_type(filename) == "application/octet-stream"
    assert should_force_download(filename) is True


def test_html_renamed_to_pdf_extension_gets_pdf_content_type_not_html():
    """safe_content_type() itself is purely filename-driven and would
    label this application/pdf if it were ever called on it — but in the
    real upload path (documents.py/products.py) content_matches_extension()
    runs FIRST and rejects real HTML content named "*.pdf" outright, so
    this file is never stored at all (see the dedicated
    content_matches_extension tests below). This test documents that the
    labeling function alone is filename-only and would not itself refuse
    the filename — it is not, on its own, the thing that stops this
    attack from reaching storage."""
    assert safe_content_type("malicious.pdf") == "application/pdf"
    assert should_force_download("malicious.pdf") is False


def test_svg_is_neutralized_even_though_it_is_a_real_image_format():
    """SVG can embed <script> — never trust it as inline-renderable content,
    regardless of correctness of the upload."""
    assert safe_content_type("logo.svg") == "application/octet-stream"
    assert should_force_download("logo.svg") is True


def test_client_supplied_mime_type_is_never_consulted():
    """The function signature itself proves this: it only ever accepts a
    filename, never a Content-Type value — there is no code path by which
    a client-supplied Content-Type could reach the stored/served value."""
    import inspect

    assert list(inspect.signature(safe_content_type).parameters) == ["filename"]


def test_unrecognized_but_harmless_extension_is_stored_as_opaque_blob():
    """A legitimate but unlisted business-document type (e.g. a CAD file)
    is still accepted — never rejected outright — just not given a
    specific renderable Content-Type."""
    assert safe_content_type("drawing.dwg") == "application/octet-stream"
    assert should_force_download("drawing.dwg") is False  # not a browser-executable type, no need to force


def test_no_extension_defaults_to_octet_stream():
    assert safe_content_type("README") == "application/octet-stream"
    assert safe_content_type(None) == "application/octet-stream"
    assert safe_content_type("") == "application/octet-stream"


def test_office_document_types_recognized():
    assert safe_content_type("spec.docx") == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert safe_content_type("pricing.xlsx") == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


# --- content_matches_extension() / detect_content_kind() -------------------
#
# These exercise the actual byte-sniffing defense that upload endpoints now
# run BEFORE storing anything — the layer that stops a mismatched file from
# ever being written to MinIO in the first place, rather than just deciding
# how to label it afterward.

_REAL_PDF_HEAD = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj"
_REAL_PNG_HEAD = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
_REAL_JPEG_HEAD = b"\xff\xd8\xff\xe0\x00\x10JFIF"
_REAL_GIF_HEAD = b"GIF89a\x01\x00\x01\x00"
_REAL_ZIP_HEAD = b"PK\x03\x04\x14\x00\x00\x00\x08\x00"  # docx/xlsx/pptx/zip all share this
_REAL_OLE_HEAD = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # legacy doc/xls/ppt
_REAL_WEBP_HEAD = b"RIFF\x00\x00\x00\x00WEBPVP8 "
_HTML_PAYLOAD = b'<html><body><script>document.title="pwned"</script></body></html>'
_SVG_PAYLOAD = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
_ELF_HEAD = b"\x7fELF\x02\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00"
_PE_HEAD = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff"


@pytest.mark.parametrize(
    "filename,head",
    [
        ("invoice.pdf", _REAL_PDF_HEAD),
        ("photo.png", _REAL_PNG_HEAD),
        ("photo.jpg", _REAL_JPEG_HEAD),
        ("photo.jpeg", _REAL_JPEG_HEAD),
        ("photo.gif", _REAL_GIF_HEAD),
        ("photo.webp", _REAL_WEBP_HEAD),
        ("spec.docx", _REAL_ZIP_HEAD),
        ("pricing.xlsx", _REAL_ZIP_HEAD),
        ("deck.pptx", _REAL_ZIP_HEAD),
        ("archive.zip", _REAL_ZIP_HEAD),
        ("legacy.doc", _REAL_OLE_HEAD),
        ("legacy.xls", _REAL_OLE_HEAD),
        ("legacy.ppt", _REAL_OLE_HEAD),
    ],
)
def test_legitimate_uploads_of_every_supported_type_are_accepted(filename, head):
    assert content_matches_extension(filename, head) is True


def test_html_content_named_with_pdf_extension_is_rejected():
    """The core polyglot/extension-spoofing attack this check exists for:
    real HTML bytes, a .pdf filename. detect_content_kind() finds no PDF
    signature (or any recognized one) in HTML content, so this is rejected
    before it's ever labeled or stored."""
    assert detect_content_kind(_HTML_PAYLOAD) is None
    assert content_matches_extension("evil.pdf", _HTML_PAYLOAD) is False


def test_html_content_named_with_image_extension_is_rejected():
    assert content_matches_extension("evil.png", _HTML_PAYLOAD) is False
    assert content_matches_extension("evil.jpg", _HTML_PAYLOAD) is False


def test_image_content_renamed_to_a_different_image_extension_is_rejected():
    """A real JPEG named .png (or vice versa) still disagrees with its
    claimed extension — rejected, not silently accepted as "close enough"."""
    assert content_matches_extension("actually_jpeg.png", _REAL_JPEG_HEAD) is False
    assert content_matches_extension("actually_png.jpg", _REAL_PNG_HEAD) is False


def test_pdf_extension_with_a_completely_different_real_format_is_rejected():
    assert content_matches_extension("not_really.pdf", _REAL_PNG_HEAD) is False
    assert content_matches_extension("not_really.pdf", _REAL_ZIP_HEAD) is False


def test_docx_extension_with_legacy_ole_content_is_rejected():
    """.docx must be a zip container, not the older OLE .doc format —
    a mismatch even though both are "real" Word documents."""
    assert content_matches_extension("report.docx", _REAL_OLE_HEAD) is False


@pytest.mark.parametrize("filename", ["photo.png", "photo.jpg", "invoice.pdf", "archive.zip", "spec.docx"])
def test_executable_content_is_always_rejected_regardless_of_claimed_extension(filename):
    """An ELF/PE/Mach-O binary renamed to look like any accepted document
    or image type — the "executable disguised as an image" attack."""
    assert content_matches_extension(filename, _ELF_HEAD) is False
    assert content_matches_extension(filename, _PE_HEAD) is False


def test_shebang_script_content_is_rejected_even_with_a_safe_extension():
    assert content_matches_extension("script.pdf", b"#!/bin/sh\nrm -rf /\n") is False


def test_svg_payload_is_accepted_but_still_force_downloaded():
    """SVG has no fixed binary signature to check (it's XML/text) — so
    content_matches_extension() has nothing to reject here. The actual
    defense for SVG's <script> risk is should_force_download(), which
    forces it to download rather than render inline regardless of
    content — verified separately above."""
    assert content_matches_extension("logo.svg", _SVG_PAYLOAD) is True
    assert should_force_download("logo.svg") is True


def test_text_and_csv_have_no_signature_requirement():
    """Free-form text content is exactly what .txt/.csv are for — nothing
    to sniff, and they're always served as text/plain/text/csv, never
    executed, so no rejection here."""
    assert content_matches_extension("notes.txt", _HTML_PAYLOAD) is True
    assert content_matches_extension("data.csv", b"a,b,c\n1,2,3\n") is True


def test_unrecognized_extension_with_arbitrary_content_is_still_accepted():
    """An extension outside this policy's allowlist (e.g. a CAD file) has
    no expected signature defined, so arbitrary content is accepted —
    matches safe_content_type()'s "unknown extension -> opaque blob,
    still accepted" policy. Executable content is still caught by the
    universal executable-signature check regardless."""
    assert content_matches_extension("drawing.dwg", b"some arbitrary CAD file bytes") is True
    assert content_matches_extension("drawing.dwg", _ELF_HEAD) is False


def test_empty_upload_content_matches_only_extensions_with_no_signature_requirement():
    assert content_matches_extension("notes.txt", b"") is True
    assert content_matches_extension("invoice.pdf", b"") is False
