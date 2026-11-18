"""
Loop iteration 22, Part A: the quotation XLSX must print with all 7 columns
(No, Item, Qty, Unit, Price, Amount, Remark) on every page. Without a page
setup Excel split the ~10.6in-wide sheet after column E on A4 portrait.

The unit tests check the page-setup flags; the print tests convert the real
workbook with LibreOffice (the stand-in for Excel, which can't run here) and
inspect the PDF pages with PyMuPDF.
"""
import shutil
import subprocess
from types import SimpleNamespace

import pytest
from openpyxl import load_workbook

from app.core.export_quotation import generate_quotation_xlsx
from tests.test_letterhead_header import LETTERHEAD_COMPANY, banner
from tests.test_quotation_visual_style import COMPANY, CUSTOMER, DIRECTOR, _document, _line_item, _png

COLUMN_TITLES = ("No", "Item", "Qty", "Unit", "Price", "Amount", "Remark")
RIGHT_MARGIN_PT = 0.5 * 72


def _items(n, tax_rate=None, remark="", sub_items=(), described=False):
    rows = []
    for i in range(1, n + 1):
        item = _line_item(i)
        item.tax_rate, item.remark = tax_rate, remark
        if sub_items:
            item.product_id = f"p{i}"
        # a real product row: bold name + a two-line description below it
        product = {"name": f"Product {i}", "description": "Line one of the description\nLine two of the description"} if described else None
        rows.append((item, product, list(sub_items)))
    return rows


def _xlsx(n=3, **kw):
    items = _items(n, **{k: kw.pop(k) for k in ("tax_rate", "remark", "sub_items", "described") if k in kw})
    kw.setdefault("company", COMPANY)
    kw.setdefault("director", DIRECTOR)
    return generate_quotation_xlsx(_document(), CUSTOMER, items, **kw).getvalue()


def _last_row(ws):
    return max(c.row for row in ws.iter_rows() for c in row if c.value is not None)


# ---- A1: page setup flags -----------------------------------------------------

@pytest.mark.parametrize("n", [3, 45])
def test_sheet_is_fit_to_one_a4_portrait_page_wide(n, tmp_path):
    path = tmp_path / "q.xlsx"
    path.write_bytes(_xlsx(n, logo_bytes=_png(), seal_bytes=_png(), director_seal_bytes=_png()))
    ws = load_workbook(path).active
    assert ws.sheet_properties.pageSetUpPr.fitToPage is True
    assert ws.page_setup.fitToWidth == 1
    assert ws.page_setup.fitToHeight == 0
    assert ws.page_setup.paperSize == 9  # A4 (openpyxl's PAPERSIZE_A4 is the string "9")
    assert ws.page_setup.orientation == "portrait"
    assert ws.print_options.horizontalCentered is True
    assert ws.print_area == f"'{ws.title}'!$A$1:$G${_last_row(ws)}"
    header_row = next(c.row for c in ws["A"] if c.value == "No")
    assert ws.print_title_rows == f"$1:${header_row}"  # unchanged: rows 1..column titles repeat
    # every anchored image starts inside A:G and inside the print area
    for img in ws._images:
        marker = img.anchor._from
        assert marker.col <= 6 and marker.row + 1 <= _last_row(ws)


# ---- A2/A3: real print through LibreOffice ---------------------------------

soffice = shutil.which("soffice") or shutil.which("libreoffice")
needs_soffice = pytest.mark.skipif(not soffice, reason="LibreOffice (soffice) is not installed in this image — print check skipped")


def _print(tmp_path, xlsx_bytes):
    """XLSX -> PDF via LibreOffice; returns the open PyMuPDF document."""
    fitz = pytest.importorskip("fitz")
    src = tmp_path / "q.xlsx"
    src.write_bytes(xlsx_bytes)
    subprocess.run(
        [soffice, f"-env:UserInstallation=file://{tmp_path}/lo-profile", "--headless",
         "--convert-to", "pdf", "--outdir", str(tmp_path), str(src)],
        check=True, capture_output=True, timeout=180,
    )
    return fitz.open(tmp_path / "q.pdf")


def _words(page):
    return [SimpleNamespace(x0=w[0], x1=w[2], text=w[4]) for w in page.get_text("words")]


def _assert_every_page_has_all_columns(pdf):
    for i, page in enumerate(pdf):
        texts = {w.text for w in _words(page)}
        missing = [t for t in COLUMN_TITLES if t not in texts]
        assert not missing, f"page {i + 1} is missing column titles {missing}"
        remark = [w for w in _words(page) if w.text == "Remark"][0]
        assert remark.x1 <= page.rect.width - RIGHT_MARGIN_PT + 1, f"page {i + 1}: Remark header is cut at the right edge"


CASES = {
    "3 items": dict(n=3),
    "3 items, tax": dict(n=3, tax_rate=5),
    "3 items, sub-items": dict(n=3, sub_items=[{"sequence_number": 1, "sku": "SKU-1", "name": "Part", "unit_price": 10}]),
    "3 items, long remark": dict(n=3, remark="A long remark that wraps over several lines inside the Remark column " * 3),
    "3 items, letterhead + seal + MD seal": dict(n=3, company=LETTERHEAD_COMPANY, logo_bytes=banner(),
                                                 seal_bytes=_png(), director_seal_bytes=_png()),
}


@needs_soffice
@pytest.mark.parametrize("case", CASES)
def test_short_quotation_prints_on_one_page_with_all_columns(case, tmp_path):
    pdf = _print(tmp_path, _xlsx(**CASES[case]))
    assert pdf.page_count == 1, f"{case}: expected 1 page, got {pdf.page_count}"
    _assert_every_page_has_all_columns(pdf)
    texts = {w.text for w in _words(pdf[0])}
    assert {"Total:", "Regards,", "Phone:"} <= texts  # totals and signature block not cut off


@needs_soffice
def test_banner_seal_and_md_seal_print_on_page_one(tmp_path):
    pdf = _print(tmp_path, _xlsx(**CASES["3 items, letterhead + seal + MD seal"]))
    assert len(pdf[0].get_images()) == 3  # banner, company seal, MD seal under "Regards,"
    for img in pdf[0].get_image_info():
        x0, y0, x1, y1 = img["bbox"]
        assert x0 >= 0 and x1 <= pdf[0].rect.width - RIGHT_MARGIN_PT + 1 and y1 <= pdf[0].rect.height


LONG_EXTRAS = {"plain": {}, "tax + letterhead + seals": {"tax_rate": 5, "company": LETTERHEAD_COMPANY, "logo_bytes": banner(),
                                                     "seal_bytes": _png(), "director_seal_bytes": _png()}}


@needs_soffice
@pytest.mark.parametrize("n", [45, 120])
@pytest.mark.parametrize("extra", LONG_EXTRAS)
def test_long_quotation_repeats_all_columns_on_every_page(n, extra, tmp_path):
    # LibreOffice does not grow row heights for rich-text Item cells the way
    # Excel does, so 45 items can still fit one LO page; 120 forces several.
    pdf = _print(tmp_path, _xlsx(n, described=True, **LONG_EXTRAS[extra]))
    if n == 120:
        assert 2 <= pdf.page_count <= 4, f"unexpected page count {pdf.page_count}"
    _assert_every_page_has_all_columns(pdf)  # also rules out blank pages / right-hand-columns-only pages
    all_text = {w.text for page in pdf for w in _words(page)}
    assert {"Total:", "Regards,", "Phone:"} <= all_text
    if extra != "plain":
        assert len(pdf[0].get_images()) >= 2  # banner + seal on page 1
