"""
Regression tests for the quotation PDF's pagination/layout: the Managing
Director signature block must stay attached to page 1 whenever the
quotation fits, and the supplier/end-user header must never be repeated
onto a page that only exists because the signature block overflowed. See
docs/SECURITY_HARDENING_LOG.md's "Quotation PDF pagination fix" entry.

Root cause #1 (fixed in 6aa04e1): the supplier/end-user header lived in a
`position: running()` @page margin box, which is a FIXED-height reservation
— to avoid it colliding with body content, the @page top margin was set to
a huge, guessed 13cm on *every* page. That left only ~13.7cm of usable
body height per page, so even short/normal quotations could overflow, and
the orphaned .md-block (no page-break protection) would land alone on a
mostly-empty page 2 that still carried the fully repeated header (since
`running()` content repeats on every page unconditionally).

Root cause #2 (found via real-world testing after root cause #1 shipped):
6aa04e1's own fix wrapped .terms + .md-block in a `.closing-section` with
break-inside: avoid, meant to keep the signature block attached. In
practice WeasyPrint sized that wrapper as one atomic unit and, judging it
"wouldn't fit" in the remaining page-1 space, moved the *entire* group to
page 2 — even when the actual remaining space was clearly larger than the
group needed, leaving most of page 1 blank.

Root cause #3 (found via a second round of real-world testing, after
root cause #2's own fix shipped): that fix replaced the wrapper with
break-after: avoid on .terms paired with break-before: avoid on
.md-block. This produced the SAME class of bug again — a large unused
gap on page 1 before Terms+MD — apparently from the engine needing to
look ahead and satisfy both hints together before committing to a
break point. It also left .terms without any break-inside protection at
all, so the Terms paragraph itself was observed splitting mid-sentence
across two pages (e.g. "Price Validity" through "Delivery: DDP" staying
on page 1, "Warranty..." continuing alone on page 2) — never intended,
and never tested for directly until now.

Settled on the simplest approach that actually holds up: each of .terms
and .md-block only protects itself with its own break-inside: avoid (so
neither can be split apart internally), with no break hint pointing at
its neighbor. Ordinary document flow then places them wherever room
allows — together on page 1 whenever there's space (the common case,
now that the @page top margin is a realistic 1.5cm instead of root
cause #1's guessed 13cm), or MD following directly after Terms at the
top of page 2 only when a page 1 genuinely doesn't have room for both.

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


def _document(terms=None, subtotal=None, tax_total=None, total=None):
    return SimpleNamespace(
        issue_date=date(2026, 1, 1),
        currency="USD",
        doc_number="QT-0001",
        terms_and_conditions=terms,
        subtotal=subtotal,
        tax_total=tax_total,
        total=total,
    )


def _line_item(name, qty=1, price=100, remark="", tax_rate=None):
    return SimpleNamespace(
        description=name,
        quantity=qty,
        unit_price=price,
        unit="Nos",
        remark=remark,
        product_id=None,
        tax_rate=tax_rate,
    )


def _pages_text(pdf_bytes):
    reader = PdfReader(pdf_bytes)
    return [page.extract_text() or "" for page in reader.pages]


def _generate(n_items, terms=None, director=DIRECTOR, tax_rate=None, subtotal=None, tax_total=None, total=None):
    items = [(_line_item(f"Item {i}", tax_rate=tax_rate), None, []) for i in range(1, n_items + 1)]
    buffer = generate_quotation_pdf(
        _document(terms=terms, subtotal=subtotal, tax_total=tax_total, total=total),
        CUSTOMER,
        items,
        company=COMPANY,
        director=director,
    )
    assert buffer.getvalue()[:4] == b"%PDF", "generate_quotation_pdf must still return a valid PDF"
    return _pages_text(buffer)


def _assert_md_page_not_orphaned(text, n_items):
    # The running footer alone ("Customer Service" + email + phone) is
    # already present on every page and runs well under 120 chars — this
    # threshold only passes when something substantial beyond MD + footer
    # (Terms text, item rows) shares the page.
    assert len(text.strip()) > 160, (
        f"with {n_items} items: found a page carrying the Managing Director block "
        f"with suspiciously little else on it (looks orphaned). Page text was: {text!r}"
    )


def test_short_quotation_keeps_md_on_page_1():
    pages = _generate(2)
    assert len(pages) == 1, f"a 2-item quotation should fit on one page, got {len(pages)}"
    assert "Managing Director" in pages[0]
    assert "Zaw Zaw" in pages[0]


def test_normal_quotation_keeps_md_correctly_positioned():
    """10 items plus the full default Terms and Conditions text is right
    around where a single A4 page's content area runs out — this
    deliberately doesn't hardcode an exact page count (that's a function
    of font metrics, not something this fix controls), but whichever page
    the content lands on, it must be *correctly* positioned: attached to
    Terms (never alone), and the supplier/end-user header must still
    appear exactly once, on page 1."""
    pages = _generate(10)
    assert len(pages) <= 2, f"a 10-item quotation should not need more than two pages, got {len(pages)}"

    md_pages = [i for i, text in enumerate(pages) if "Managing Director" in text]
    assert md_pages == [len(pages) - 1], "Managing Director block should be on the last page"
    md_page = pages[md_pages[0]]
    assert "Zaw Zaw" in md_page
    _assert_md_page_not_orphaned(md_page, n_items=10)

    header_pages = [i for i, text in enumerate(pages) if "SUPPLIER" in text and "END USER" in text]
    assert header_pages == [0], f"expected the header exactly once, on page 1 — found it on page(s) {header_pages}"


def test_long_quotation_paginates_cleanly_md_not_orphaned_alone():
    pages = _generate(80)
    assert len(pages) >= 2, "an 80-item quotation is expected to genuinely need more than one page"

    md_pages = [i for i, text in enumerate(pages) if "Managing Director" in text]
    assert md_pages, "Managing Director block must appear somewhere in the document"
    md_page = pages[md_pages[0]]

    # The MD block must never be the only real content on its page. Every
    # page also carries the running footer ("Customer Service" + email +
    # phone, ~90 chars) regardless of what else is on it, so a page with
    # only MD + footer sits well under this threshold — anything genuinely
    # attached (item rows, Terms text) pushes well past it.
    _assert_md_page_not_orphaned(md_page, n_items=80)


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
                _assert_md_page_not_orphaned(text, n_items)


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


def test_terms_text_never_splits_mid_paragraph_across_pages():
    """Root cause #3: without break-inside: avoid on .terms itself, a
    multi-line Terms paragraph could be sliced apart mid-sentence by the
    pagination engine — the early lines staying on one page, the rest
    continuing alone on the next. Every line must land on the same page
    as every other line, regardless of how many items or how much
    surrounding content there is."""
    terms = "\n".join(f"Term line {i} of the agreement." for i in range(1, 9))
    for n_items in (1, 10, 40):
        pages = _generate(n_items, terms=terms)
        terms_pages = [i for i, text in enumerate(pages) if "Term line 1 of the agreement." in text]
        assert len(terms_pages) == 1, (
            f"with {n_items} items: Terms text should start on exactly one page, "
            f"found its first line on page(s) {terms_pages}"
        )
        page_text = pages[terms_pages[0]]
        for i in range(1, 9):
            assert f"Term line {i} of the agreement." in page_text, (
                f"with {n_items} items: Terms line {i} is missing from the page its "
                f"first line is on — the paragraph looks like it split across pages. "
                f"Page text was: {page_text!r}"
            )


def test_pdf_shows_tax_breakdown_when_document_has_tax():
    """Regression: the PDF's Total row used to be a plain sum of each
    line's quantity * unit_price, completely ignoring tax — a document
    created with a tax rate showed no trace of it in the exported PDF.
    generate_quotation_pdf must now render Subtotal/Tax/Total using the
    document's own stored (and authoritative) subtotal/tax_total/total,
    not a value it recomputes itself."""
    pages = _generate(3, tax_rate=10, subtotal=300, tax_total=30, total=330)
    full_text = "\n".join(pages)
    assert "Subtotal:" in full_text
    assert "Tax (10%):" in full_text
    assert "330.00" in full_text, "the grand total (subtotal + tax) must appear in the PDF"
    assert "30.00" in full_text, "the tax amount itself must appear in the PDF"


def test_pdf_omits_tax_breakdown_when_document_has_no_tax():
    """A document with no tax (the common case) should show a plain
    Total, not a zero-tax breakdown that implies tax was considered and
    happened to be nothing."""
    pages = _generate(3, tax_rate=0, subtotal=300, tax_total=0, total=300)
    full_text = "\n".join(pages)
    assert "Subtotal:" not in full_text
    assert "Tax (" not in full_text
    assert "300.00" in full_text


def test_pdf_tax_breakdown_falls_back_when_document_predates_tax_total():
    """A document saved before tax_total existed on the model (subtotal/
    tax_total/total all None) must still render a sensible Total —
    falling back to the sum of its line items — rather than crashing or
    showing a blank/garbage total."""
    pages = _generate(3)  # no subtotal/tax_total/total, no tax_rate — all defaults
    full_text = "\n".join(pages)
    assert "Subtotal:" not in full_text
    assert "300.00" in full_text, "3 items at 100 each should still total 300.00"
