"""
Loop iterations 31-33: bigger images in the quotation XLSX + four glitches.

Sizes are fractions of the table width; on the default 7-column sheet
(987 px) they are the user's drawn targets: banner box 625 x 140 from column
A (iteration 34: 677 x 197), squared logo 170 px, company seal 190 px centred in the last column, MD seal box
180 x 200 under "Regards,". Glitches: #### in big totals, clipped two-line
column titles, a visible leading quote on "+959..." values, clipped "Contact No".
"""
import io
import math

import pytest
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
from openpyxl.utils.units import EMU_to_pixels

from app.core.export_quotation import generate_quotation_xlsx
from tests.test_letterhead_header import LETTERHEAD_COMPANY, banner
from tests.test_quotation_visual_style import COMPANY, CUSTOMER, DIRECTOR, _document, _line_item, _png
from tests.test_xlsx_print_setup import _print, needs_soffice

MD = dict(DIRECTOR, position="Managing Director")


def _ws(items=None, labour=False, **kw):
    doc = _document()
    doc.include_labour = labour
    items = items or [(_line_item(i), None, []) for i in (1, 2, 3)]
    kw.setdefault("company", COMPANY)
    return load_workbook(io.BytesIO(generate_quotation_xlsx(doc, CUSTOMER, items, **kw).getvalue())).active


def _col_px(ws):
    n = ws.max_column
    return [int((256 * ws.column_dimensions[get_column_letter(c)].width + 128 // 7) / 256 * 7) for c in range(1, n + 1)]


def _row_px(ws, r):
    return (ws.row_dimensions[r].height or 15) * 4 / 3


def _rect(ws, img):
    """(x0, x1, first row, last row) the image covers; rows 1-based."""
    a, col_px = img.anchor, _col_px(ws)
    x0 = sum(col_px[:a._from.col]) + EMU_to_pixels(a._from.colOff)
    w, h = EMU_to_pixels(a.ext.width), EMU_to_pixels(a.ext.height)
    first = r = a._from.row + 1
    y = _row_px(ws, r)
    while y < h:
        r += 1
        y += _row_px(ws, r)
    return x0, x0 + w, first, r


def _size(img):
    return EMU_to_pixels(img.anchor.ext.width), EMU_to_pixels(img.anchor.ext.height)


def _title_row(ws):
    return next(c.row for c in ws["A"] if c.value == "QUOTATION")


# ---- A1: sizes on the default layout (user's targets, +-3 px) ------------------

def test_default_layout_banner_seal_and_md_seal_sizes():
    ws = _ws(company=LETTERHEAD_COMPANY, logo_bytes=banner((1600, 300)), seal_bytes=_png(),
             director=MD, director_seal_bytes=_png((180, 200)))
    assert sum(_col_px(ws)) == 987
    lh, seal, md = ws._images
    w, h = _size(lh)
    assert w == pytest.approx(677, abs=3) and h == pytest.approx(127, abs=3)  # 1600x300 fitted in 677x197
    assert _rect(ws, lh)[0] == pytest.approx(3, abs=3), "banner starts at column A's left edge"
    assert _size(seal) == pytest.approx((190, 190), abs=3)
    x0, x1, _, _ = _rect(ws, seal)
    col_px = _col_px(ws)
    assert (x0 + x1) / 2 == pytest.approx(sum(col_px[:-1]) + col_px[-1] / 2, abs=3), "seal centred in the last column"
    assert _rect(ws, lh)[1] <= x0 - 25, "at least 25 px between banner and seal"
    assert _size(md) == pytest.approx((180, 200), abs=3)
    _assert_md_seal_in_last_column(ws, md)


def _assert_md_seal_in_last_column(ws, md):
    """Iteration 32: the MD seal sits inside the last column (where the user
    drew it): left edge 4 px right of the column's left edge, never past the
    table's right edge; "Regards," above it in that column."""
    col_px = _col_px(ws)
    x0, x1, r0, _ = _rect(ws, md)
    assert x0 == pytest.approx(sum(col_px[:-1]) + 4, abs=3), "seal starts 4 px inside the last column"
    assert x1 <= sum(col_px), "seal ends inside the table"
    regards = ws.cell(r0 - 1, len(col_px))
    assert regards.value == "Regards," and regards.alignment.horizontal == "center"
    # the column is ~35 px wider than the seal, so the text centre is ~13 px right of the seal's
    assert sum(col_px[:-1]) + col_px[-1] / 2 == pytest.approx((x0 + x1) / 2, abs=15)


@pytest.mark.parametrize("size", [(720, 190), (200, 400), (300, 300)])
def test_images_keep_their_aspect_ratio(size):
    ws = _ws(company=LETTERHEAD_COMPANY, logo_bytes=banner(size), seal_bytes=_png(size),
             director=MD, director_seal_bytes=_png(size))
    for img, box in zip(ws._images, [(677, 197), (190, 190), (180, 200)]):
        w, h = _size(img)
        assert w / h == pytest.approx(size[0] / size[1], rel=0.02)
        assert w <= box[0] + 1 and h <= box[1] + 1
        assert w == pytest.approx(box[0], abs=1) or h == pytest.approx(box[1], abs=1), "fills its box"


def test_labour_columns_scale_the_images_with_the_table():
    ws = _ws(labour=True, company=LETTERHEAD_COMPANY, logo_bytes=banner((1600, 300)), seal_bytes=_png(),
             director=MD, director_seal_bytes=_png((180, 200)))
    col_px = _col_px(ws)
    scale = sum(col_px) / 987
    assert scale > 1.1
    lh, seal, md = ws._images
    assert _size(lh)[0] == pytest.approx(677 * scale, abs=3)
    assert _rect(ws, lh)[1] <= _rect(ws, seal)[0] - 25
    assert _size(seal) == pytest.approx((190 * scale, 190 * scale), abs=3)
    assert _size(md) == pytest.approx((180 * scale, 200 * scale), abs=3)
    x0, x1, _, _ = _rect(ws, seal)
    # 190 * scale is wider than column I, so the seal is pulled left to end at the table's edge.
    assert x1 == pytest.approx(sum(col_px), abs=1) and x0 >= sum(col_px[:-2]), "in I, clamped to the table edge"
    for img in ws._images:
        assert _rect(ws, img)[1] <= sum(col_px), "inside A:I"
    _assert_md_seal_in_last_column(ws, md)


def test_md_seal_is_pulled_left_when_the_last_column_is_narrow():
    """A seal wider than the last column must not stick out past the table."""
    ws = _ws(labour=True, director=MD, director_seal_bytes=_png((180, 200)))
    col_px = _col_px(ws)
    (md,) = ws._images
    assert _size(md)[0] + 4 > col_px[-1], "labour layout: scaled seal (208 px) + 4 > last column (210 px)"
    assert _rect(ws, md)[1] == pytest.approx(sum(col_px), abs=1), "right edge clamped to the table's edge"


@pytest.mark.parametrize("size, expected", [((1600, 300), (677, 127)), ((1000, 400), (492, 197))])
@pytest.mark.parametrize("seal", [True, False])
def test_banner_fits_677x197_from_column_a_with_an_empty_row_above_the_title(size, expected, seal):
    """Iteration 34: the user's red box; same box with or without the seal. Rows are
    reserved for the whole box, so a short banner does not pull the title up."""
    ws = _ws(company=LETTERHEAD_COMPANY, logo_bytes=banner(size), seal_bytes=_png() if seal else None)
    lh = ws._images[0]
    assert _size(lh) == pytest.approx(expected, abs=3)
    x0, _, _, last = _rect(ws, lh)
    assert x0 == pytest.approx(3, abs=3)
    assert last < _title_row(ws) - 1, "at least one empty row between the banner and the title"
    assert (_title_row(ws) - 1) * 20 >= 197 + 20, "title below the whole 197 px box plus an empty row"


# ---- A2: nothing covers text ------------------------------------------------------

VARIANTS = {
    "letterhead + seal + MD seal": dict(company=LETTERHEAD_COMPANY, logo_bytes=banner(), seal_bytes=_png(), director=MD, director_seal_bytes=_png()),
    "logo + seal + MD seal": dict(logo_bytes=_png(), seal_bytes=_png(), director=MD, director_seal_bytes=_png((180, 200))),
    "letterhead, no seal": dict(company=LETTERHEAD_COMPANY, logo_bytes=banner((1600, 120)), director=MD, director_seal_bytes=_png()),
    "seal only, no MD seal": dict(seal_bytes=_png(), director=MD),
    "labour + letterhead + seals": dict(labour=True, company=LETTERHEAD_COMPANY, logo_bytes=banner(), seal_bytes=_png(), director=MD, director_seal_bytes=_png()),
}


@pytest.mark.parametrize("variant", VARIANTS)
def test_no_image_covers_text_or_another_image(variant):
    ws = _ws(**VARIANTS[variant])
    col_px = _col_px(ws)
    rects = [_rect(ws, img) for img in ws._images]
    merged = {r.min_row * 1000 + r.min_col: r for r in ws.merged_cells.ranges}
    for row in ws.iter_rows():
        for c in row:
            if c.value is None or type(c).__name__ == "MergedCell":
                continue
            rng = merged.get(c.row * 1000 + c.column)
            c0, c1 = (rng.min_col, rng.max_col) if rng else (c.column, c.column)
            cx0, cx1 = sum(col_px[:c0 - 1]), sum(col_px[:c1])
            for x0, x1, r0, r1 in rects:
                assert not (r0 <= c.row <= r1 and x0 < cx1 and cx0 < x1), f"image covers {c.coordinate} {c.value!r}"
    for i, a in enumerate(rects):
        for b in rects[i + 1:]:
            assert not (a[0] < b[1] and b[0] < a[1] and a[2] <= b[3] and b[2] <= a[3]), "images overlap"
    header = [r for r in rects if r[2] == 1]
    if header:
        assert max(r[3] for r in header) < _title_row(ws), "header images end above the title row"


# ---- A4: no #### ----------------------------------------------------------------

def _huge_items():
    item = _line_item(1)
    item.unit_price = 9999999999.99
    return [(item, None, [])]


@pytest.mark.parametrize("labour", [False, True])
def test_money_columns_have_a_40_percent_margin_over_the_biggest_total(labour):
    items = _huge_items()
    items[0][0].labour_unit_price = 5
    ws = _ws(items, labour=labour)
    longest = len(f"{9999999999.99 + (5 if labour else 0):,.2f}")  # labour adds to the grand total
    cols = "EFGH" if labour else "EF"
    for col in cols:
        assert ws.column_dimensions[col].width >= 1.4 * longest + 2
    totals = [c for row in ws.iter_rows() for c in row if isinstance(c.value, str) and c.value.startswith("=")]
    assert totals and all(c.alignment.shrink_to_fit for c in totals if c.column_letter == "F")


def test_small_amounts_keep_the_16_wide_money_columns():
    ws = _ws()
    assert ws.column_dimensions["E"].width == ws.column_dimensions["F"].width == 16


@needs_soffice
def test_huge_total_prints_without_hashes(tmp_path):
    xlsx = generate_quotation_xlsx(_document(), CUSTOMER, _huge_items(), company=COMPANY).getvalue()
    text = _print(tmp_path, xlsx)[0].get_text()
    assert "9,999,999,999.99" in text and "#" not in text


# ---- A4: two-line column titles ----------------------------------------------

def test_column_title_row_is_tall_enough_for_two_lines():
    ws = _ws()
    r = next(c.row for c in ws["A"] if c.value == "No")
    assert ws.row_dimensions[r].height >= 30
    assert ws[f"E{r}"].value == "Price (USD)" and ws[f"F{r}"].value == "Amount (USD)"
    assert ws[f"F{r}"].alignment.wrap_text


# ---- A5: no visible quote, injection still blocked -------------------------------

def test_phone_numbers_show_as_typed_and_formulas_stay_text():
    item = _line_item(1)
    item.remark = "=cmd|' /c calc'!A1"
    ws = _ws([(item, None, [])], company=dict(COMPANY, contact_no="+959958645521"),
             director=dict(MD, contact_no="-5"))
    cells = {c.value: c for row in ws.iter_rows() for c in row if isinstance(c.value, str)}
    for value in ("+959958645521", "-5", "=cmd|' /c calc'!A1"):
        assert value in cells, value
        assert cells[value].data_type == "s", f"{value} must be text, not a formula"
    assert not [v for v in cells if v.startswith("'")], "no visible leading quote"


def test_supplier_labels_shrink_to_fit_column_a():
    ws = _ws(director=MD)
    label = next(c for c in ws["A"] if c.value == "Contact No")
    assert label.alignment.shrink_to_fit


# ---- Iteration 34: END USER in the last column, Qty/Unit centred ----------------

@pytest.mark.parametrize("labour", [False, True])
def test_end_user_block_in_the_last_column_aligned_with_supplier(labour):
    ws = _ws(labour=labour, director=MD)
    last = get_column_letter(ws.max_column)
    sup = next(c.row for c in ws["A"] if isinstance(c.value, str) and c.value.startswith("SUPPLIER"))
    assert ws[f"{last}{sup}"].value == "END USER"
    labels = [str(ws[f"{last}{sup + i}"].value).split(":")[0] for i in range(1, 5)]
    assert labels == ["Customer Name", "Quotation ID", "Organization", "Address"]
    assert ws[f"A{sup + 1}"].value == "Name", "supplier rows line up with the end-user rows"
    for col in "EF" if not labour else "EFGH":
        assert all(ws[f"{col}{r}"].value is None for r in range(sup, sup + 5)), f"nothing left in {col}"


def test_long_end_user_address_wraps_onto_more_lines():
    customer = dict(CUSTOMER, address="No. 123, Very Long Street Name, Some Township, Yangon Region, Myanmar 11181")
    ws = load_workbook(io.BytesIO(generate_quotation_xlsx(
        _document(), customer, [(_line_item(1), None, [])], company=COMPANY, director=MD).getvalue())).active
    cell = next(c for c in ws["G"] if isinstance(c.value, str) and c.value.startswith("Address: "))
    assert cell.value.endswith("Myanmar 11181") and cell.alignment.wrap_text
    assert ws.row_dimensions[cell.row].height >= 2 * 13


@pytest.mark.parametrize("labour", [False, True])
def test_qty_and_unit_are_centred_on_item_and_sub_item_rows(labour):
    sub = {"sequence_number": 1, "sku": "S1", "name": "Sub", "unit": "No", "unit_price": 1}
    ws = _ws([(_line_item(1), None, [sub]), (_line_item(2), None, [])], labour=labour)
    head = next(c.row for c in ws["A"] if c.value == "No") + (2 if labour else 1)
    rows = [r for r in range(head, ws.max_row + 1) if ws[f"D{r}"].value][:3]
    assert len(rows) == 3, "two items and one sub-item"
    for r in rows:
        assert ws[f"C{r}"].alignment.horizontal == ws[f"D{r}"].alignment.horizontal == "center"
        assert ws[f"C{r}"].alignment.vertical == "top"
    assert ws[f"E{rows[0]}"].alignment.horizontal is None, "price stays right-aligned (number default)"
