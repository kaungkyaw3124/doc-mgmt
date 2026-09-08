"""
Security regression tests for Priority 5 — Secure Upload Content-Type.

These test the pure decision logic (app.core.upload_safety) that
documents.py's upload/download endpoints now use instead of trusting the
client-supplied Content-Type. No DB/MinIO needed — these are pure
filename-in, decision-out functions.
"""

import pytest

from app.core.upload_safety import safe_content_type, should_force_download


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
    """The classic extension-spoofing attack: real HTML content, but named
    with a .pdf extension. Content-Type is derived from the filename
    server-side, so this is stored/served as application/pdf — a browser
    told Content-Type: application/pdf does not execute embedded
    <script> the way it would if served as text/html. (X-Content-Type-Options:
    nosniff, added in Task 8, is what stops the browser from re-sniffing
    and overriding that declared type.)"""
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
