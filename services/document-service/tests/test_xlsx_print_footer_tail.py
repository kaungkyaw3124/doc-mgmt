"""
Loop iteration 35 — Excel printing (user's screenshots 3-5):
- the Customer Service / Email / Phone footer prints at the bottom of EVERY
  page (Excel page footer), like the PDF's running footer, and only once;
- Terms and Conditions + "Regards," + the MD seal print together on the last
  page and the seal never crosses a page break. They share one row, because
  neither Excel nor LibreOffice ever splits a row (Excel ignores manual page
  breaks while "Fit to 1 page wide" is on, so a row break could not do it).
"""
import io
import math
import zipfile

import pytest
from openpyxl import load_workbook
from openpyxl.utils.units import EMU_to_pixels

from app.core.export_quotation import MAX_ROW_PT, _footer_lines, generate_quotation_xlsx
from tests.test_quotation_visual_style import COMPANY, CUSTOMER, DIRECTOR, _document, _line_item, _png
from tests.test_xlsx_print_setup import _print, _xlsx, needs_soffice

MD = dict(DIRECTOR, position="Managing Director")


def _ws(data):
    return load_workbook(io.BytesIO(data)).active


def _find(ws, text):
    return [c for row in ws.iter_rows() for c in row if isinstance(c.value, str) and text in c.value]


# ---- footer ---------------------------------------------------------------------

def test_footer_lines_like_the_pdf():
    assert _footer_lines(COMPANY) == ["Customer Service", "Email: sales@acme.example", "Phone: +95 1 234 5678"]
    assert _footer_lines(dict(COMPANY, support_email="")) == ["Customer Service", "Phone: +95 1 234 5678"]
    assert _footer_lines(dict(COMPANY, support_email=None, contact_no=None)) == []
    assert _footer_lines(None) == []


def test_footer_is_set_escaped_and_has_room():
    data = _xlsx(company=dict(COMPANY, support_email="r&d@acme.example", contact_no="+959958645521"))
    ws = _ws(data)
    text = ws.oddFooter.left.text
    assert text.splitlines() == ["&BCustomer Service&B", "Email: r&&d@acme.example", "Phone: +959958645521"]
    assert "'" not in text
    assert ws.oddFooter.left.size == 8
    xml = zipfile.ZipFile(io.BytesIO(data)).read("xl/worksheets/sheet1.xml").decode()
    assert "_x000a_" not in xml, "line breaks must be real (LibreOffice prints _x000a_ literally)"
    footer_xml = xml[xml.index("<oddFooter>"):xml.index("</oddFooter>")]
    assert len(footer_xml) - len("<oddFooter>") <= 255 + 20  # Excel's limit (+ XML escaping of &)
    # 3 lines at 8pt (~0.45in) on top of the 0.2in footer margin fit inside the bottom margin
    assert ws.page_margins.bottom >= ws.page_margins.footer + 3 * 10 / 72 + 0.2
    assert ws.page_margins.top == 0.3 and ws.page_margins.left == ws.page_margins.right == 0.5


def test_no_support_contact_means_no_footer_and_normal_margin():
    ws = _ws(_xlsx(company=dict(COMPANY, support_email=None, contact_no=None)))
    assert not ws.oddFooter.left.text
    assert ws.page_margins.bottom == 0.5


def test_print_area_ends_before_the_on_screen_customer_service_block():
    ws = _ws(_xlsx(director_seal_bytes=_png()))
    (cs,) = _find(ws, "Customer Service")
    end = int(ws.print_area.rsplit("$", 1)[1])
    assert end < cs.row
    (terms,) = _find(ws, "Terms and Conditions")
    assert terms.row == end, "the print area ends with the tail row"


# ---- tail in one row -------------------------------------------------------------

@pytest.mark.parametrize("labour", [False, True])
def test_terms_regards_and_seal_share_one_row(labour):
    doc = _document()
    doc.include_labour = labour
    ws = _ws(generate_quotation_xlsx(doc, CUSTOMER, [(_line_item(1), None, [])], company=COMPANY,
                                     director=MD, director_seal_bytes=_png((200, 220))).getvalue())
    (terms,) = _find(ws, "Terms and Conditions")
    assert "Customer is responsible for any bank transaction charges." in terms.value
    regards = ws.cell(terms.row, 9 if labour else 7)
    assert regards.value.endswith("Regards,") and regards.alignment.vertical == "top" == terms.alignment.vertical
    # "Regards," on the first 10pt line below the last Terms line (12pt heading + n lines; iteration 38)
    assert regards.value.count("\n") == math.ceil((15.75 + terms.value.count("\n") * 12.75) / 12.75)
    (seal,) = ws._images
    assert seal.anchor._from.row + 1 == terms.row, "seal anchored in the tail row"
    row_pt = ws.row_dimensions[terms.row].height
    seal_top_pt = EMU_to_pixels(seal.anchor._from.rowOff) * 0.75
    seal_bottom_pt = seal_top_pt + EMU_to_pixels(seal.anchor.ext.height) * 0.75
    regards_bottom_pt = (regards.value.count("\n") + 1) * 12.75  # one 10pt run
    assert regards_bottom_pt <= seal_top_pt, "seal below the Regards line"
    assert seal_bottom_pt < row_pt <= MAX_ROW_PT, "seal fully inside the row (never cut by a page break)"


def test_without_a_seal_name_and_position_are_in_the_tail_row():
    ws = _ws(_xlsx(director=MD))
    (terms,) = _find(ws, "Terms and Conditions")
    assert ws.cell(terms.row, 7).value.endswith(f"Regards,\n{MD['name']}\nManaging Director")


def test_very_long_terms_spill_into_rows_above_the_tail_row():
    doc = _document()
    doc.terms_and_conditions = "\n".join(f"Term line {i}" for i in range(1, 61))
    ws = _ws(generate_quotation_xlsx(doc, CUSTOMER, [(_line_item(1), None, [])], company=COMPANY,
                                     director=MD, director_seal_bytes=_png()).getvalue())
    (heading,) = _find(ws, "Terms and Conditions")
    tail_row = _find(ws, "Term line 60")[0].row
    assert heading.row < tail_row
    assert ws.row_dimensions[tail_row].height <= MAX_ROW_PT
    text = "\n".join(c.value for row in ws.iter_rows() for c in row if isinstance(c.value, str))
    assert all(f"Term line {i}\n" in text + "\n" for i in range(1, 61)), "no Terms line lost"
    assert ws._images[0].anchor._from.row + 1 == tail_row


# ---- real print through LibreOffice -----------------------------------------------

def _check_pages(pdf, case):
    last = pdf.page_count - 1
    for i, page in enumerate(pdf):
        text = page.get_text()
        assert text.count("Customer Service") == 1, f"{case}: page {i + 1} footer count {text.count('Customer Service')}"
        assert "Email: sales@acme.example" in text and "Phone: +95 1 234 5678" in text, f"{case}: page {i + 1}"
        words = page.get_text("words")
        assert max(w[3] for w in words if w[4] == "Service") > page.rect.height - 72, f"{case}: footer at the page bottom"
        has_tail = "Terms and Conditions" in text or "Regards," in text
        assert has_tail == (i == last), f"{case}: tail on page {i + 1} of {pdf.page_count}"
    page = pdf[last]
    regards = [w for w in page.get_text("words") if w[4] == "Regards,"]
    assert len(regards) == 1
    seals = [img["bbox"] for img in page.get_image_info() if img["bbox"][1] > regards[0][3] - 1]
    assert len(seals) == 1, f"{case}: MD seal under Regards on the last page"
    x0, y0, x1, y1 = seals[0]
    footer_top = max(w[1] for w in page.get_text("words") if w[4] == "Customer")  # the lowest "Customer" is the footer
    assert regards[0][3] <= y0 and y1 < footer_top, f"{case}: seal between Regards and the footer"
    # a seal cut by a page break prints shorter: its 200x220 shape would change
    assert (x1 - x0) / (y1 - y0) == pytest.approx(200 / 220, rel=0.03), f"{case}: seal printed whole"
    terms_y = [w[1] for w in page.get_text("words") if w[4] == "Terms"][0]
    assert terms_y < regards[0][1]


@needs_soffice
@pytest.mark.parametrize("n", [3, 45, 50, 54, 58, 62, 66, 70, 120])
def test_footer_on_every_page_and_tail_together_on_the_last(n, tmp_path):
    # 50..70 plain items walk the end of the table across the page-1 boundary,
    # which used to cut the seal or leave Regards on another page (img 4/5).
    pdf = _print(tmp_path, _xlsx(n, director=MD, director_seal_bytes=_png((200, 220))))
    if n == 120:
        assert pdf.page_count >= 2
    _check_pages(pdf, f"{n} items")
