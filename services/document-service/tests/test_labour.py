"""
Loop iteration 23 — labour cost per line item ("Include labour").

A document with include_labour on gets Material and Labour column groups,
a SUB Total row (materials | labour), an optional "Tax on materials" row and
a Grand Total row, in the API totals, the PDF and the XLSX. Tax is on
materials only; labour is never taxed. include_labour off = unchanged output
(the existing layout/XLSX tests cover that and are untouched).

The 29-line fixture is synthetic: three lines copied from the user's
screenshot (CCTV, 9U rack, GI pipe) plus filler lines chosen so the totals
are the screenshot's: SUB Total 31,756,300 | 3,813,000, Grand 35,569,300.
"""
import io
from datetime import date
from types import SimpleNamespace

import pytest
from openpyxl import load_workbook
from pypdf import PdfReader

from app.core.export_pdf import generate_quotation_pdf
from app.core.export_quotation import generate_quotation_xlsx
from tests.test_manual_end_user import ctx  # noqa: F401  (fixture: API on SQLite)
from tests.test_quotation_visual_style import COMPANY, CUSTOMER, DIRECTOR, MM, _png, _walk
from tests.test_xlsx_print_setup import _print, _words, needs_soffice


def screenshot_lines():
    """(description, qty, material unit price, labour unit price or None) x 29."""
    lines = [("CCTV camera", 27, 220000, 20000), ("9U rack", 1, 951000, 60000), ("GI pipe", 1, 150000, 50000)]
    lines += [(f"Fitting {i:02d}", 1, 950000, 150000 if i <= 20 else None) for i in range(1, 26)]
    lines += [("Cable drum", 2, 482650, 81500)]
    assert len(lines) == 29
    return lines


def _payload(lines, include_labour=True, tax_rate=0):
    items = []
    for desc, qty, price, labour in lines:
        item = {"description": desc, "quantity": qty, "unit_price": price, "tax_rate": tax_rate, "remark": desc}
        if labour is not None:
            item["labour_unit_price"] = labour
        items.append(item)
    return {"doc_type": "quotation", "currency": "MMK", "issue_date": str(date(2026, 11, 18)),
            "include_labour": include_labour, "items": items}


# ---- A1: totals math (API, server-side) --------------------------------------

def test_screenshot_totals_and_client_totals_are_ignored(ctx):
    client, _ = ctx
    body = {**_payload(screenshot_lines()), "subtotal": 1, "tax_total": 1, "total": 1}  # client totals: ignored
    r = client.post("/documents", json=body)
    assert r.status_code == 201, r.text
    doc = r.json()
    assert doc["include_labour"] is True
    assert float(doc["subtotal"]) == 31756300
    assert float(doc["tax_total"]) == 0
    assert float(doc["total"]) == 35569300
    labour = sum(float(i["quantity"]) * float(i["labour_unit_price"] or 0) for i in doc["items"])
    assert labour == 3813000
    cctv = doc["items"][0]
    assert float(cctv["line_total"]) == 5940000 and float(cctv["labour_unit_price"]) == 20000
    assert doc["items"][-2]["labour_unit_price"] is None  # a line without labour stays empty


def test_tax_is_on_materials_only(ctx):
    client, _ = ctx
    r = client.post("/documents", json=_payload([("Switch", 1, 1000000, 200000)], tax_rate=5))
    doc = r.json()
    assert float(doc["subtotal"]) == 1000000
    assert float(doc["tax_total"]) == 50000  # 5% of materials, not of labour
    assert float(doc["total"]) == 1250000


def test_decimal_quantity_and_prices(ctx):
    client, _ = ctx
    doc = client.post("/documents", json=_payload([("Cable", 2.5, 100.40, 10.20), ("Big", 1, 99999999.99, 1)])).json()
    assert float(doc["subtotal"]) == pytest.approx(2.5 * 100.40 + 99999999.99)
    assert float(doc["total"]) == pytest.approx(2.5 * 100.40 + 99999999.99 + 2.5 * 10.20 + 1)


def test_negative_labour_price_is_rejected(ctx):
    client, _ = ctx
    assert client.post("/documents", json=_payload([("X", 1, 10, -1)])).status_code == 422


def test_labour_stored_but_unused_when_switched_off(ctx):
    client, _ = ctx
    doc = client.post("/documents", json=_payload([("Switch", 2, 100, 30)], include_labour=False)).json()
    assert doc["include_labour"] is False
    assert float(doc["items"][0]["labour_unit_price"]) == 30  # stored
    assert float(doc["total"]) == 200  # not used
    pdf_text = PdfReader(io.BytesIO(client.get(f"/documents/{doc['id']}/export/quotation-pdf").content)).pages[0].extract_text()
    assert "Labour" not in pdf_text and "SUB Total" not in pdf_text and "Total:" in pdf_text
    ws = load_workbook(io.BytesIO(client.get(f"/documents/{doc['id']}/export/quotation").content)).active
    assert not any(c.value and "Labour" in str(c.value) for row in ws.iter_rows() for c in row)
    assert ws.max_column == 7


def test_update_recalculates_and_keeps_the_flag_when_omitted(ctx):
    client, _ = ctx
    doc = client.post("/documents", json=_payload([("Switch", 2, 100, 30)])).json()
    assert float(doc["total"]) == 260
    items = _payload([("Switch", 3, 100, 30)])["items"]
    r = client.patch(f"/documents/{doc['id']}", json={"items": items, "total": 1})  # include_labour omitted: kept
    assert r.status_code == 200, r.text
    assert r.json()["include_labour"] is True and float(r.json()["total"]) == 390
    r = client.patch(f"/documents/{doc['id']}", json={"items": items, "include_labour": False})
    assert r.json()["include_labour"] is False and float(r.json()["total"]) == 300


# ---- PDF ---------------------------------------------------------------------

def _doc_ns(lines, tax_rate=0):
    sub = sum(q * p for _, q, p, _ in lines)
    lab = sum(q * (l or 0) for _, q, _, l in lines)
    tax = sub * tax_rate / 100
    return SimpleNamespace(issue_date=date(2026, 11, 18), currency="MMK", doc_number="QT-LAB-1", doc_type="quotation",
                           terms_and_conditions=None, include_labour=True, subtotal=sub, tax_total=tax, total=sub + lab + tax)


def _items_ns(lines, tax_rate=0):
    return [(SimpleNamespace(description=d, quantity=q, unit_price=p, labour_unit_price=l, unit="Nos", remark="",
                             product_id=None, tax_rate=tax_rate), None, []) for d, q, p, l in lines]


def _html_and_pages(lines, monkeypatch, tax_rate=0, **kw):
    """Renders the labour PDF; returns (pypdf page texts, WeasyPrint box tree pages)."""
    from weasyprint import HTML as RealHTML
    from app.core import export_pdf
    captured = {}

    class _Spy:
        def __init__(self, string=None, **k):
            captured["html"] = string
            self._real = RealHTML(string=string, **k)

        def write_pdf(self, *a, **k):
            return self._real.write_pdf(*a, **k)

    monkeypatch.setattr(export_pdf, "HTML", _Spy)
    pdf = generate_quotation_pdf(_doc_ns(lines, tax_rate), CUSTOMER, _items_ns(lines, tax_rate),
                                 company=COMPANY, director=DIRECTOR, **kw)
    texts = [p.extract_text() or "" for p in PdfReader(pdf).pages]
    boxes = [list(_walk(p._page_box)) for p in RealHTML(string=captured["html"]).render().pages]
    return texts, boxes


def _cell(boxes, text):
    cells = [b for b in boxes if type(b).__name__ == "TableCellBox"
             and "".join(getattr(x, "text", "") for x in _walk(b)).strip() == text]
    assert cells, f"no table cell {text!r}"
    return cells[0]


def _span(cell):
    return cell.border_box_x(), cell.border_box_x() + cell.border_width()


def test_pdf_screenshot_totals_header_and_widths(monkeypatch):
    texts, pages = _html_and_pages(screenshot_lines(), monkeypatch)
    joined = "\n".join(texts)
    for s in ("SUB Total", "Grand Total", "31,756,300.00", "3,813,000.00", "35,569,300.00",
              "5,940,000.00", "540,000.00", "Material (MMK)", "Labour (MMK)"):
        assert s in joined, s
    assert "Tax on materials" not in joined  # no tax -> no tax row
    p1 = pages[0]
    material, labour = _cell(p1, "Material (MMK)"), _cell(p1, "Labour (MMK)")
    unit_prices = sorted((c for c in p1 if type(c).__name__ == "TableCellBox"
                          and "".join(getattr(x, "text", "") for x in _walk(c)).strip() in ("Unit Price", "Total Price")),
                         key=lambda c: c.border_box_x())[:4]
    assert _span(material) == pytest.approx((_span(unit_prices[0])[0], _span(unit_prices[1])[1]), abs=0.5)
    assert _span(labour) == pytest.approx((_span(unit_prices[2])[0], _span(unit_prices[3])[1]), abs=0.5)
    # No / Item / Qty / Unit / Remark span both header rows
    assert _cell(p1, "Remark").border_height() > 1.5 * material.border_height()
    # every cell inside the 184.6mm content width (12.7mm side margins)
    left, right = 12.7 * MM, (210 - 12.7) * MM
    for page in pages:
        for c in page:
            if type(c).__name__ == "TableCellBox":
                x0, x1 = _span(c)
                assert x0 >= left - 0.5 and x1 <= right + 0.5, f"cell outside content width: {x0:.1f}-{x1:.1f}"


def test_pdf_zero_labour_shows_dash_and_empty_price(monkeypatch):
    texts, pages = _html_and_pages([("Only material", 1, 500, None), ("With labour", 1, 500, 100)], monkeypatch)
    assert _cell(pages[0], "-")  # labour total of the line with no labour
    row = min((b for b in pages[0] if type(b).__name__ == "TableRowBox"
               and "Only material" in "".join(getattr(x, "text", "") for x in _walk(b))),
              key=lambda b: b.border_height())  # the item row, not the outer page-layout row
    cells = [c for c in row.children if type(c).__name__ == "TableCellBox"]
    assert len(cells) == 9
    assert "".join(getattr(x, "text", "") for x in _walk(cells[6])).strip() == ""  # labour unit price empty


def test_pdf_tax_row_is_on_materials(monkeypatch):
    texts, _ = _html_and_pages([("Switch", 1, 1000000, 200000)], monkeypatch, tax_rate=5)
    t = texts[-1]
    assert "Tax on materials (5%)" in t and "50,000.00" in t and "1,250,000.00" in t


@pytest.mark.parametrize("n", [3, 45])
def test_pdf_tail_and_repeating_header(n, monkeypatch):
    lines = [(f"Line {i:02d}", 1, 1000, 100 if i % 2 else None) for i in range(1, n)] + [("LASTROW", 1, 1000, 100)]
    texts, _ = _html_and_pages(lines, monkeypatch, director_seal_bytes=_png())
    final = texts[-1]
    for marker in ("LASTROW", "SUB Total", "Grand Total", "Terms and Conditions", "Regards,"):
        assert marker in final, f"N={n}: {marker!r} not on the last page"
    for i, t in enumerate(texts):
        assert "Material (MMK)" in t and "Labour (MMK)" in t, f"page {i + 1}: header not repeated"
        if i < len(texts) - 1:
            assert "Grand Total" not in t
    if n == 45:
        assert len(texts) >= 2


# ---- XLSX --------------------------------------------------------------------

def _xlsx(lines, tax_rate=0, **kw):
    kw.setdefault("company", COMPANY)
    kw.setdefault("director", DIRECTOR)
    return generate_quotation_xlsx(_doc_ns(lines, tax_rate), CUSTOMER, _items_ns(lines, tax_rate), **kw).getvalue()


def _find(ws, value):
    return next(c for row in ws.iter_rows() for c in row if c.value == value)


def test_xlsx_headers_formulas_and_totals_rows(tmp_path):
    ws = load_workbook(io.BytesIO(_xlsx(screenshot_lines(), seal_bytes=_png(), director_seal_bytes=_png()))).active
    h = _find(ws, "No").row
    merges = {str(m) for m in ws.merged_cells.ranges}
    assert {f"E{h}:F{h}", f"G{h}:H{h}", f"A{h}:A{h + 1}", f"B{h}:B{h + 1}", f"I{h}:I{h + 1}"} <= merges
    assert ws[f"E{h}"].value == "Material (MMK)" and ws[f"G{h}"].value == "Labour (MMK)"
    assert [ws.cell(h + 1, c).value for c in range(5, 9)] == ["Unit Price", "Total Price", "Unit Price", "Total Price"]
    assert ws.print_title_rows == f"$1:${h + 1}"
    first = h + 2
    assert ws[f"H{first}"].value == f"=C{first}*G{first}"
    assert ws[f"G{first + 26}"].value is None  # "Fitting 24": no labour -> empty price
    sub = _find(ws, "SUB Total").row
    grand = _find(ws, "Grand Total").row
    assert ws[f"F{sub}"].value.startswith("=F") and ws[f"H{sub}"].value.startswith("=H")
    assert ws[f"F{grand}"].value == f"=F{sub}+H{sub}"
    assert f"F{grand}:H{grand}" in merges and f"A{sub}:E{sub}" in merges
    for r in (sub, grand):
        for c in range(1, 10):
            cell = ws.cell(r, c)
            assert cell.border.top.style == "thin" and cell.border.bottom.style == "thin", (r, c)
            if type(cell).__name__ != "MergedCell":  # merged-over cells hold no text
                assert cell.font.b, (r, c)
    assert ws[f"A{sub}"].alignment.horizontal == "center"
    assert ws.print_area.endswith(f"$A$1:$I${max(c.row for row in ws.iter_rows() for c in row if c.value is not None)}")
    for img in ws._images:
        assert img.anchor._from.col <= 8  # inside A:I
    seal = [img for img in ws._images if img.anchor._from.row == 0]
    assert seal and seal[0].anchor._from.col == 7  # company seal at the top right (H)


def test_xlsx_tax_row(tmp_path):
    ws = load_workbook(io.BytesIO(_xlsx([("Switch", 1, 1000000, 200000)], tax_rate=5))).active
    sub, tax, grand = _find(ws, "SUB Total").row, _find(ws, "Tax on materials (5%)").row, _find(ws, "Grand Total").row
    assert ws[f"F{tax}"].value == f"=F{sub}*5.0/100"
    assert ws[f"F{grand}"].value == f"=F{sub}+H{sub}+F{tax}"


@needs_soffice
@pytest.mark.parametrize("case", ["screenshot", "tax", "45 lines"])
def test_xlsx_formulas_compute_and_all_columns_print(case, tmp_path):
    lines = {"screenshot": screenshot_lines(), "tax": [("Switch", 1, 1000000, 200000)],
             "45 lines": [(f"Line {i}", 1, 1000, 100) for i in range(1, 46)]}[case]
    pdf = _print(tmp_path, _xlsx(lines, tax_rate=5 if case == "tax" else 0, seal_bytes=_png(), director_seal_bytes=_png()))
    words = [w.text for page in pdf for w in _words(page)]
    expected = {"screenshot": ["31,756,300.00", "3,813,000.00", "35,569,300.00", "5,940,000.00", "540,000.00"],
                "tax": ["1,000,000.00", "200,000.00", "50,000.00", "1,250,000.00"],
                "45 lines": ["45,000.00", "4,500.00", "49,500.00"]}[case]
    for value in expected:
        assert value in words, f"{case}: {value} not computed by LibreOffice"
    if case == "screenshot":
        assert "-" in words  # zero labour total shows "-"
    for i, page in enumerate(pdf):
        texts = [w.text for w in _words(page)]
        for title in ("No", "Item", "Qty", "Unit", "Material", "Labour", "Remark"):
            assert title in texts, f"{case}, page {i + 1}: {title} missing"
        remark = [w for w in _words(page) if w.text == "Remark"][0]
        assert remark.x1 <= page.rect.width - 0.5 * 72 + 1
