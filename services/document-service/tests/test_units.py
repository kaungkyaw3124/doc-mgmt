"""
Loop iteration 30 — units on document lines. One helper (app.core.units)
decides what is printed: "No"/"Nos" by quantity (No for exactly 1), any other
unit as stored. Checked here in the helper, the PDF (rendered HTML + PDF text),
the XLSX cells, and on save (a product line without a unit takes the product's).
"""
import html
import re
import uuid
from types import SimpleNamespace

import pytest
from openpyxl import load_workbook
from pypdf import PdfReader

from app.core import export_pdf
from app.core.export_pdf import generate_quotation_pdf
from app.core.export_quotation import generate_quotation_xlsx
from app.core.units import display_unit
from app.routers import documents as documents_router
from tests.test_group_scope import AS_OPS, _create, ctx  # noqa: F401
from tests.test_labour import _doc_ns
from tests.test_quotation_visual_style import COMPANY, CUSTOMER, DIRECTOR


@pytest.mark.parametrize("unit,qty,shown", [
    ("No", 1, "No"), ("No", 2, "Nos"), ("Nos", 1, "No"), ("no", 0, "Nos"), ("No", 2.5, "Nos"),
    ("NOS", 3, "Nos"), ("No", 1.0, "No"), ("M", 1, "M"), ("M", 5, "M"), ("Set", 2, "Set"),
    ("", 1, "No"), (None, 4, "Nos"),
])
def test_display_unit(unit, qty, shown):
    from decimal import Decimal
    assert display_unit(unit, qty) == shown
    assert display_unit(unit, Decimal(str(qty))) == shown  # the ORM gives Decimal("1.00")


LINES = [("Patch cord", 1, "No"), ("Wall plate", 2, "No"), ("Cable 2.5mm", 3, "M"), ("Old line", 1, "Nos")]
EXPECTED = {"Patch cord": "No", "Wall plate": "Nos", "Cable 2.5mm": "M", "Old line": "No"}
SUBS = [{"sequence_number": 1, "name": "Copper wire", "sku": "W-1", "unit_price": 2, "unit": "M"},
        {"sequence_number": 2, "name": "Lug", "sku": "L-1", "unit_price": 1, "unit": "No"}]


def _items():
    items = []
    for i, (name, qty, unit) in enumerate(LINES):
        line = SimpleNamespace(description=name, quantity=qty, unit_price=10, labour_unit_price=None, unit=unit,
                               remark="", product_id=uuid.uuid4() if i == 0 else None, tax_rate=0)
        items.append((line, {"name": name} if i == 0 else None, SUBS if i == 0 else []))
    return items


def _doc():
    doc = _doc_ns([(n, q, 10, None) for n, q, _ in LINES])
    doc.include_labour = False
    return doc


def test_pdf_units(monkeypatch):
    captured = {}
    real = export_pdf.HTML

    def spy(string=None, **kw):
        captured["html"] = string
        return real(string=string, **kw)

    monkeypatch.setattr(export_pdf, "HTML", spy)
    pdf = generate_quotation_pdf(_doc(), CUSTOMER, _items(), company=COMPANY, director=DIRECTOR)
    rows = [[html.unescape(re.sub(r"<[^>]+>", " ", c)).split() for c in re.findall(r"<td[^>]*>(.*?)</td>", r, re.S)]
            for r in re.findall(r"<tr[^>]*>(.*?)</tr>", captured["html"], re.S)]
    unit_by_name = {}
    for cells in rows:
        if len(cells) >= 4:
            text = " ".join(cells[1])
            for name in [*EXPECTED, "Copper wire", "Lug"]:
                if name in text:
                    unit_by_name[name] = " ".join(cells[3])
    assert unit_by_name == {**EXPECTED, "Copper wire": "M", "Lug": "No"}
    text = " ".join(p.extract_text() for p in PdfReader(pdf).pages)
    assert "Nos" in text and " M " in f" {text} "


def test_xlsx_units():
    ws = load_workbook(generate_quotation_xlsx(_doc(), CUSTOMER, _items())).active
    found = {}
    for row in ws.iter_rows(min_col=2, max_col=4):
        text = str(row[0].value or "")
        for name in [*EXPECTED, "Copper wire", "Lug"]:
            if name in text:
                found[name] = row[2].value
    assert found == {**EXPECTED, "Copper wire": "M", "Lug": "No"}


def test_product_line_takes_the_products_unit_and_manual_line_defaults_to_no(ctx, monkeypatch):
    client, _ = ctx
    monkeypatch.setattr(documents_router, "get_product",
                        lambda pid, scope=None: {"name": "Cable", "unit": "M", "unit_price": "4"})
    doc = _create(client, "/documents", AS_OPS, items=[
        {"product_id": str(uuid.uuid4()), "quantity": "3"},                   # unit from the product
        {"product_id": str(uuid.uuid4()), "quantity": "1", "unit": "Roll"},   # the form's choice wins
        {"description": "Labour", "quantity": "1", "unit_price": "9"},        # manual: "No"
    ])
    assert [i["unit"] for i in doc["items"]] == ["M", "Roll", "No"]
