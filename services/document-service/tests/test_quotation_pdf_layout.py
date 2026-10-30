"""
Regression tests for the quotation PDF's pagination/layout: the Managing
Director signature block must stay attached to page 1 whenever the
quotation fits, and must never land alone on a page. See
docs/SECURITY_HARDENING_LOG.md's "Quotation PDF pagination fix" entry.

Loop iteration 2 reversed one earlier rule on purpose: the header (logo,
seal, QUOTATION, Date, SUPPLIER / END USER) now repeats at the top of
EVERY page, as the <thead> of an outer layout table in quotation.html — NOT
via the `running()` margin box described in root cause #1 below, which
needed a guessed fixed top margin. The orphan checks therefore strip the
repeated header/footer text before measuring what else is on a page.

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

Root cause #4 (found via real CI, not just real-world testing this
time — three follow-up attempts after #3's fix, each removing the
break-after/break-before pairing in a different way: independent
break-inside:avoid on both blocks, no break rules on .md-block at all,
and a completely different box model for .md-block): all three
reproduced the EXACT same MD-orphaned-alone failure, meaning the
pairing itself was never the problem — root cause #3's actual defect
was solely .terms' missing break-inside: avoid (which caused the
mid-sentence split). The break-after/break-before pairing was
incorrectly removed instead of just adding break-inside: avoid
alongside it. Fixed by restoring the pairing (.terms gets
break-inside: avoid AND break-after: avoid; .md-block gets
break-before: avoid) — the one combination that had never actually
been tried, since #3's fix always removed the pairing before #4's
follow-ups could test it together with .terms' break-inside: avoid.

This is why every one of these fixes is verified against real
WeasyPrint output via CI before being reported as working — the
symptom (MD landing alone) looks identical whether the cause is a
missing break rule, a wrong pairing, or an unrelated box-model quirk,
and only real rendered output distinguishes between them.

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


# Last line of the repeated header (the END USER address) and the repeated
# items-table column titles, as pypdf extracts them.
HEADER_END = "Address No. 99, Customer Street, Mandalay"
COLUMN_TITLES = "No Item Qty Unit Price (USD) Amount (USD) Remark"
FOOTER_START = "Customer Service\nEmail:"


def _page_body(text):
    """A page's text minus what now repeats on every page regardless of
    content: the header (everything up to the END USER address), the items
    table's column titles, and the running footer. Without this, the ~350
    chars of header on every page would make an MD-only page look full."""
    assert HEADER_END in text, f"page is missing the repeated header: {text!r}"
    body = text.split(HEADER_END, 1)[1]
    body = body.split(FOOTER_START, 1)[0]
    return body.replace(COLUMN_TITLES, "").strip()


def _assert_md_page_not_orphaned(text, n_items):
    # With header, column titles and footer stripped, an MD-only page is
    # just "Zaw Zaw" + "Managing Director" (~25 chars); even a lone Total row
    # plus MD stays under ~45. Anything genuinely attached (Terms text, item
    # rows) is well past this threshold.
    body = _page_body(text)
    assert len(body) > 60, (
        f"with {n_items} items: found a page carrying the Managing Director block "
        f"with suspiciously little else on it (looks orphaned). Page body was: {body!r}"
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
    Terms (never alone), and the supplier/end-user header must be at the
    top of every page."""
    pages = _generate(10)
    assert len(pages) <= 2, f"a 10-item quotation should not need more than two pages, got {len(pages)}"

    md_pages = [i for i, text in enumerate(pages) if "Managing Director" in text]
    assert md_pages == [len(pages) - 1], "Managing Director block should be on the last page"
    md_page = pages[md_pages[0]]
    assert "Zaw Zaw" in md_page
    _assert_md_page_not_orphaned(md_page, n_items=10)

    header_pages = [i for i, text in enumerate(pages) if "SUPPLIER" in text and "END USER" in text]
    assert header_pages == list(range(len(pages))), f"expected the header on every page — found it on page(s) {header_pages}"


def test_long_quotation_paginates_cleanly_md_not_orphaned_alone():
    pages = _generate(80)
    assert len(pages) >= 2, "an 80-item quotation is expected to genuinely need more than one page"

    md_pages = [i for i, text in enumerate(pages) if "Managing Director" in text]
    assert md_pages, "Managing Director block must appear somewhere in the document"
    md_page = pages[md_pages[0]]

    # The MD block must never be the only real content on its page.
    # Every page also carries the repeated header and running footer
    # regardless of what else is on it; _assert_md_page_not_orphaned strips
    # those before measuring.
    _assert_md_page_not_orphaned(md_page, n_items=80)


def test_header_and_parties_repeat_on_every_page():
    """Inverted in loop iteration 2 (was ..._are_never_duplicated_onto_a_later_page):
    the user wants the whole header on every page, including a last page
    holding only Terms / MD signature."""
    for n_items in (2, 10, 80):
        pages = _generate(n_items)
        for n, text in enumerate(pages, 1):
            for needle in ("QUOTATION", f"SUPPLIER: {COMPANY['name']}", "END USER",
                           f"Name {DIRECTOR['name']}", "QT-0001"):
                assert needle in text, (
                    f"with {n_items} items: page {n} of {len(pages)} is missing {needle!r} from the header"
                )
            assert text.count("QUOTATION") == 1, f"with {n_items} items: page {n} has the title more than once"


def test_orphan_check_still_catches_an_md_only_page_under_the_repeated_header():
    """Guard for the helper itself: now that every page carries ~350 chars
    of header, a page holding ONLY header + MD block + footer must still be
    reported as orphaned. Built from a real page's header and footer."""
    real = _generate(1)[0]
    header = real.split(HEADER_END, 1)[0] + HEADER_END
    footer = FOOTER_START + real.split(FOOTER_START, 1)[1]
    md_only = f"{header}\nZaw Zaw\nManaging Director\n{footer}"
    with pytest.raises(AssertionError, match="orphaned"):
        _assert_md_page_not_orphaned(md_only, n_items=0)
    with pytest.raises(AssertionError, match="orphaned"):  # lone Total row + MD, under the column titles
        _assert_md_page_not_orphaned(f"{header}\n{COLUMN_TITLES}\nTotal: 8,000.00\nZaw Zaw\nManaging Director\n{footer}", n_items=0)


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
