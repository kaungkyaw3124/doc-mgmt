"""
Regression tests for the quotation PDF's pagination/layout: the Managing
Director signature block must stay attached to page 1 whenever the
quotation fits, and the supplier/end-user header must never be repeated
onto a page that only exists because the signature block overflowed. See
docs/SECURITY_HARDENING_LOG.md's "Quotation PDF pagination fix" entry.

Root cause (before this fix): the supplier/end-user header lived in a
`position: running()` @page margin box, which is a FIXED-height reservation
— to avoid it colliding with body content, the @page top margin was set to
a huge, guessed 13cm on *every* page. That left only ~13.7cm of usable
body height per page, so even short/normal quotations could overflow, and
the orphaned .md-block (no page-break protection) would land alone on a
mostly-empty page 2 that still carried the fully repeated header (since
`running()` content repeats on every page unconditionally).

No live Postgres or Docker needed — generate_quotation_pdf is called
directly with lightweight fake objects (same pattern as
test_delete_permission.py), and the resulting PDF bytes are inspected with
pypdf. Requires WeasyPrint's system libs, already installed by this job
(see .github/workflows/tests.yml's document-service job) before
`pip install -r requirements-dev.txt` (which now includes pypdf).
"""
from datetime import date
from types import SimpleNamespace

import pytest
from pypdf import PdfReader

from app.core.export_pdf import generate_quotation_pdf

COMPANY = {
    "name": "Acme Supplier Co., Ltd.",
    "address": "No. 1, Sample Road, Yangon",
    "contact_no": "+95 1 234 5678",
    "support_email": "sales@acme.example",
}

CUSTOMER = {
    "id": "11111111-1111-1111-1111-111111111111",
    "name": "Beta Customer Co., Ltd.",
    "billing_address": {"line1": "No. 99, Customer Street", "city": "Mandalay"},
}

DIRECTOR = {
    "name": "Zaw Zaw",
    "address": "N/A",
    "contact_no": "N/A",
    "email": "director@acme.example",
}


def _document(terms=None):
    return SimpleNamespace(
        issue_date=date(2026, 1, 1),
        currency="USD",
        doc_number="QT-0001",
        terms_and_conditions=terms,
    )


def _line_item(name, qty=1, price=100, remark=""):
    return SimpleNamespace(
        description=name,
        quantity=qty,
        unit_price=price,
        unit="Nos",
        remark=remark,
        product_id=None,
    )


def _items(n):
    return [(_line_item(f"Item {i}"), None, []) for i in range(1, n + 1)]


def _pages_text(pdf_bytes):
    reader = PdfReader(pdf_bytes)
    return [page.extract_text() or "" for page in reader.pages]


def _generate(n_items, terms=None, director=DIRECTOR):
    buffer = generate_quotation_pdf(
        _document(terms=terms),
        CUSTOMER,
        _items(n_items),
        company=COMPANY,
        director=director,
    )
    assert buffer.getvalue()[:4] == b"%PDF", "generate_quotation_pdf must still return a valid PDF"
    return _pages_text(buffer)


def test_short_quotation_keeps_md_on_page_1():
    pages = _generate(2)
    assert len(pages) == 1, f"a 2-item quotation should fit on one page, got {len(pages)}"
    assert "Managing Director" in pages[0]
    assert "Zaw Zaw" in pages[0]


def test_normal_quotation_keeps_md_correctly_positioned():
    pages = _generate(10)
    assert len(pages) == 1, f"a 10-item quotation should still fit on one page, got {len(pages)}"
    assert "Managing Director" in pages[0]
    assert "Zaw Zaw" in pages[0]
    assert "Terms and Conditions" in pages[0]


def test_long_quotation_paginates_cleanly_md_not_orphaned_alone():
    pages = _generate(80)
    assert len(pages) >= 2, "an 80-item quotation is expected to genuinely need more than one page"

    md_pages = [i for i, text in enumerate(pages) if "Managing Director" in text]
    assert md_pages, "Managing Director block must appear somewhere in the document"
    md_page = pages[md_pages[0]]

    # The MD block must never be the only thing on its page — it must be
    # grouped with (at minimum) the terms section it's wrapped together
    # with in the template.
    assert "Terms and Conditions" in md_page, (
        "the Managing Director block appears to be alone on its page "
        "(missing the Terms and Conditions section it should be grouped with)"
    )
    assert len(md_page.strip()) > len("Zaw Zaw\nManaging Director"), (
        "the MD page has suspiciously little content — looks orphaned"
    )


def test_header_and_parties_are_never_duplicated_onto_a_later_page():
    for n_items in (2, 10, 80):
        pages = _generate(n_items)
        header_pages = [i for i, text in enumerate(pages) if "SUPPLIER" in text and "END USER" in text]
        assert header_pages == [0], (
            f"with {n_items} items: expected the SUPPLIER/END USER header to appear exactly once, "
            f"on page 1 only — found it on page(s) {header_pages} (out of {len(pages)} total)"
        )
        title_count = sum(text.count("QUOTATION") for text in pages)
        assert title_count == 1, (
            f"with {n_items} items: expected the QUOTATION title to appear exactly once, "
            f"got {title_count} occurrences across {len(pages)} page(s)"
        )


def test_no_md_only_page_across_a_range_of_lengths():
    """Sweep a range of item counts around the old failure zone and assert
    no page ever contains the MD block with (effectively) nothing else."""
    for n_items in (1, 3, 5, 8, 12, 20, 30):
        pages = _generate(n_items)
        for text in pages:
            if "Managing Director" in text:
                assert "Terms and Conditions" in text, (
                    f"with {n_items} items: found a page containing the Managing Director "
                    f"block without the Terms and Conditions section — looks orphaned. "
                    f"Page text was: {text!r}"
                )


def test_pdf_export_still_renders_expected_content():
    """Basic regression check that the layout refactor didn't break what
    actually gets rendered — company, customer, items, and total."""
    pages = _generate(3, terms="Custom terms line one.\nCustom terms line two.")
    full_text = "\n".join(pages)
    assert COMPANY["name"] in full_text
    assert CUSTOMER["name"] in full_text
    assert "Item 1" in full_text
    assert "Custom terms line one." in full_text
    assert "Custom terms line two." in full_text


def test_quotation_without_director_has_no_md_block_and_still_renders():
    pages = _generate(3, director=None)
    full_text = "\n".join(pages)
    assert "Managing Director" not in full_text
    assert "Terms and Conditions" in full_text
