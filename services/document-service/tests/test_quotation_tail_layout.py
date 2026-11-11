"""
Loop iteration 14: the quotation's "tail" — the last item (with its
sub-item rows), Subtotal / Tax / Total, Terms and Conditions and the MD's
"Regards," + seal — must always land together on the LAST page. Before
this, a multi-page quotation could end a page with the Total row and push
Terms + the signature onto a page of their own (the seal sometimes onto a
third page). Also checks the removed "Items" group row stays gone, in the
PDF and the XLSX.

Same approach as test_quotation_pdf_layout.py: the real
generate_quotation_pdf with lightweight fake objects, pages read with
pypdf. No Docker or database needed.
"""
import re
from datetime import date
from io import BytesIO
from types import SimpleNamespace

import pytest
from openpyxl import load_workbook
from PIL import Image
from pypdf import PdfReader

from app.core.export_pdf import generate_quotation_pdf
from app.core.export_quotation import generate_quotation_xlsx

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
DIRECTOR = {"name": "Zaw Zaw", "position": "Managing Director", "address": "N/A", "contact_no": "N/A"}
LAST = "LASTROW"  # unique marker in the last item's name


def _seal_png():
    buf = BytesIO()
    Image.new("RGB", (300, 300), (200, 30, 30)).save(buf, format="PNG")
    return buf.getvalue()


SEAL = _seal_png()


def _document(tax=False):
    return SimpleNamespace(
        issue_date=date(2026, 1, 1), currency="USD", doc_number="QT-0001",
        terms_and_conditions=None,
        subtotal=None, tax_total=(10.0 if tax else None), total=None,
    )


def _item(name, tax=False, category=None):
    item = SimpleNamespace(description=name, quantity=1, unit_price=100, unit="Nos", remark="",
                           product_id=None, tax_rate=(10 if tax else None))
    product = {"name": name, "category": category} if category else None
    return item, product


def _subs(n):
    return [{"sequence_number": i, "sku": f"SKU-{i}", "name": f"Sub part {i}", "unit_price": 5} for i in range(1, n + 1)]


def _items(n, tax=False, last_subs=0, category=None):
    out = []
    for i in range(1, n + 1):
        name = f"Widget {i:03d}" if i < n else f"{LAST} {i:03d}"
        item, product = _item(name, tax=tax, category=category)
        out.append((item, product, _subs(last_subs) if i == n else []))
    return out


def _render(n, tax=False, seal=False, last_subs=0):
    buf = generate_quotation_pdf(
        _document(tax), CUSTOMER, _items(n, tax=tax, last_subs=last_subs),
        company=COMPANY, director=DIRECTOR, director_seal_bytes=SEAL if seal else None,
    )
    return [p.extract_text() or "" for p in PdfReader(buf).pages]


VARIANTS = {
    "plain": {},
    "tax": {"tax": True},
    "seal": {"seal": True},
    "seal_tax_subs": {"seal": True, "tax": True, "last_subs": 3},
}

# Page counts of the same renders on the code BEFORE this change (commit
# 6d754c8), N = 1..45. Moving the tail as one block may add at most one page.
BASELINE_PAGES = {
    "plain": [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 3],
    "tax": [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 3, 3, 3],
    "seal": [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 3, 3, 3, 3, 3],
    "seal_tax_subs": [1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3],
}


@pytest.mark.parametrize("variant", VARIANTS)
def test_tail_always_together_on_last_page(variant):
    for n in range(1, 46):
        pages = _render(n, **VARIANTS[variant])
        final = pages[-1]
        for marker in (f"{LAST} {n:03d}", "Total:", "Terms and Conditions", "Regards,"):
            assert marker in final, f"{variant}, N={n}: {marker!r} not on the final page {len(pages)}"
        for i, text in enumerate(pages):
            if "Total:" in text:
                assert f"{LAST} {n:03d}" in text, f"{variant}, N={n}: 'Total:' on page {i + 1} without the last item"
            if "Terms and Conditions" in text or "Regards," in text:
                assert f"{LAST} {n:03d}" in text, f"{variant}, N={n}: Terms/Regards on page {i + 1} without the last item"
        if VARIANTS[variant].get("last_subs"):
            assert "Sub part 3" in final, f"{variant}, N={n}: last item's sub-items not on the final page"
        before = BASELINE_PAGES[variant][n - 1]
        assert len(pages) <= before + 1, f"{variant}, N={n}: {len(pages)} pages, was {before}"


COLUMN_TITLES = "No Item Qty Unit Price (USD) Amount (USD) Remark"


@pytest.mark.parametrize("category", [None, "Networking"])
def test_no_items_group_row_in_pdf(category):
    buf = generate_quotation_pdf(_document(), CUSTOMER, _items(3, category=category), company=COMPANY, director=DIRECTOR)
    text = PdfReader(buf).pages[0].extract_text()
    # The first thing after the column titles is item 1's row, nothing in between.
    after_titles = text.split(COLUMN_TITLES, 1)[1].lstrip()
    assert re.match(r"1\s*Widget 001", after_titles), f"expected item 1 right under the header, got {after_titles[:60]!r}"
    if category:
        assert category not in text
    assert "\nItems\n" not in f"\n{text}\n"


@pytest.mark.parametrize("category", [None, "Networking"])
def test_no_items_group_row_in_xlsx(category):
    document = SimpleNamespace(**vars(_document()), title=None, notes=None)
    buf = generate_quotation_xlsx(document, CUSTOMER, _items(3, category=category), company=COMPANY, director=DIRECTOR)
    ws = load_workbook(buf).active
    header_row = next(r for r in range(1, ws.max_row + 1) if ws.cell(r, 1).value == "No" and ws.cell(r, 2).value == "Item")
    assert ws.cell(header_row + 1, 1).value == 1, "first row under the column titles must be item 1"
    values = {c.value for row in ws.iter_rows() for c in row}
    assert "Items" not in values
    if category:
        assert category not in values


def test_tall_tail_falls_back_to_normal_flow():
    # 25 sub-items on the last item: the tail can be taller than what is left
    # on a page; it must still render completely, nothing dropped.
    pages = _render(20, seal=True, tax=True, last_subs=25)
    joined = "\n".join(pages)
    for marker in (f"{LAST} 020", "Sub part 1 ", "Sub part 25", "Total:", "Terms and Conditions", "Regards,"):
        assert marker in joined, f"{marker!r} missing from the tall-tail render"
    assert len(pages) <= 4
