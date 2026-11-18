"""
Loop iteration 22, Part B: the document type decides the export heading.
An Invoice says INVOICE / "Invoice ID", a Proposal PROPOSAL / "Proposal ID",
in the PDF and the XLSX, and the sheet name and download filename follow.
Quotation output is unchanged; catalogue documents keep the Quotation wording
their quotation export always had.
"""
import io
from datetime import date

import pytest
from openpyxl import load_workbook
from pypdf import PdfReader
from weasyprint import HTML as RealHTML

from app.core import export_pdf
from app.core.export_pdf import generate_quotation_pdf
from app.core.export_quotation import doc_type_label, generate_quotation_xlsx
from tests.test_quotation_visual_style import COMPANY, CUSTOMER, DIRECTOR, MM, _document, _line_item, _png, _texts, _walk
from tests.test_supplier_position_e2e import client  # noqa: F401  (SQLite-backed API client fixture)

EXPECTED = {"quotation": "Quotation", "invoice": "Invoice", "proposal": "Proposal", "catalogue": "Quotation"}


def _doc(doc_type):
    doc = _document()
    doc.doc_type = doc_type
    return doc


def _pdf_text(pdf_bytes):
    return PdfReader(io.BytesIO(pdf_bytes)).pages[0].extract_text()


def _xlsx_heading(ws):
    """(heading, end-user ID label) from the generated sheet."""
    heading = next(c.value for c in ws["A"] if c.value and str(c.value).isupper() and c.font.color is not None and c.font.color.rgb == "00C00000")
    id_label = next(c.value for c in ws["E"] if isinstance(c.value, str) and c.value.endswith(" ID"))
    return heading, id_label


@pytest.mark.parametrize("doc_type,label", EXPECTED.items())
def test_label_helper(doc_type, label):
    assert doc_type_label(doc_type) == label


@pytest.mark.parametrize("raw,label", [(None, "Quotation"), ("", "Quotation"), ("INVOICE", "Invoice"),
                                       ("receipt", "Receipt"), ("bad/type\"\n", "Quotation"), ("фактура", "Quotation")])
def test_unknown_or_odd_types_never_crash(raw, label):
    assert doc_type_label(raw) == label
    generate_quotation_xlsx(_doc(raw), CUSTOMER, [(_line_item(1), None, [])], company=COMPANY)  # sheet name stays valid


@pytest.mark.parametrize("doc_type", ["quotation", "invoice", "proposal", "catalogue"])
def test_pdf_and_xlsx_heading_follow_the_type(doc_type):
    label = EXPECTED[doc_type]
    items = [(_line_item(1), None, [])]
    text = _pdf_text(generate_quotation_pdf(_doc(doc_type), CUSTOMER, items, company=COMPANY, director=DIRECTOR).getvalue())
    assert label.upper() in text and f"{label} ID" in text
    ws = load_workbook(generate_quotation_xlsx(_doc(doc_type), CUSTOMER, items, company=COMPANY, director=DIRECTOR)).active
    assert ws.title == label
    assert _xlsx_heading(ws) == (label.upper(), f"{label} ID")
    if label != "Quotation":
        assert "QUOTATION" not in text and "Quotation ID" not in text
        assert not any("Quotation" in str(c.value) or "QUOTATION" in str(c.value) for row in ws.iter_rows() for c in row if c.value)


def test_document_without_doc_type_still_says_quotation():
    """Older callers (and the existing layout tests) pass no doc_type."""
    ws = load_workbook(generate_quotation_xlsx(_document(), CUSTOMER, [(_line_item(1), None, [])], company=COMPANY)).active
    assert ws.title == "Quotation" and _xlsx_heading(ws) == ("QUOTATION", "Quotation ID")


@pytest.mark.parametrize("doc_type", ["proposal", "invoice"])
def test_heading_keeps_its_style_and_fits_next_to_the_banner(monkeypatch, doc_type):
    captured = {}

    class _Spy:
        def __init__(self, string=None, **kw):
            captured["html"] = string
            self._real = RealHTML(string=string, **kw)

        def write_pdf(self, *a, **kw):
            return self._real.write_pdf(*a, **kw)

    monkeypatch.setattr(export_pdf, "HTML", _Spy)
    generate_quotation_pdf(_doc(doc_type), CUSTOMER, [(_line_item(1), None, [])], company=COMPANY, director=DIRECTOR,
                           logo_bytes=_png(), seal_bytes=_png())
    page = list(_walk(RealHTML(string=captured["html"]).render().pages[0]._page_box))
    heading = [b for b in _texts(page, doc_type.upper()) if b.text == doc_type.upper()]
    assert len(heading) == 1
    box = heading[0]
    assert box.style["font_weight"] >= 700 and box.style["color"][0] > 0.6  # same bold red heading
    assert box.position_x + box.width <= (210 - 12.7) * MM  # inside the content area, one line


# ---- HTTP end-to-end ---------------------------------------------------------

@pytest.mark.parametrize("doc_type", ["quotation", "invoice", "proposal"])
def test_exports_through_the_api(client, doc_type):  # noqa: F811
    label = EXPECTED[doc_type]
    r = client.post("/documents", json={"doc_type": doc_type, "issue_date": str(date(2026, 11, 18)), "items": [
        {"description": "Router", "quantity": 1, "unit_price": 10}]})
    assert r.status_code == 201, r.text
    doc = r.json()

    pdf = client.get(f"/documents/{doc['id']}/export/quotation-pdf")
    assert pdf.status_code == 200
    assert pdf.headers["content-disposition"] == f'attachment; filename="{label}-{doc["doc_number"]}.pdf"'
    text = _pdf_text(pdf.content)
    assert label.upper() in text and f"{label} ID" in text
    if doc_type != "quotation":
        assert "QUOTATION" not in text and "Quotation ID" not in text

    xlsx = client.get(f"/documents/{doc['id']}/export/quotation")
    assert xlsx.status_code == 200
    assert xlsx.headers["content-disposition"] == f'attachment; filename="{label}-{doc["doc_number"]}.xlsx"'
    ws = load_workbook(io.BytesIO(xlsx.content)).active
    assert ws.title == label and _xlsx_heading(ws) == (label.upper(), f"{label} ID")
