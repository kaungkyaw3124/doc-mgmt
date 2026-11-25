"""
Loop iteration 31: bigger images in the quotation XLSX + four glitches.

Sizes are fractions of the table width; on the default 7-column sheet
(987 px) they are the user's drawn targets: banner box 560 x 115 from column
B, company seal / squared logo 170 px centred on the F|G boundary, MD seal box
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
    assert w == pytest.approx(560, abs=3) and h == pytest.approx(105, abs=3)  # 1600x300 fitted in 560x115
    assert _rect(ws, lh)[0] == 56, "banner starts at column B's left edge"
    assert _size(seal) == pytest.approx((170, 170), abs=3)
    x0, x1, _, _ = _rect(ws, seal)
    assert (x0 + x1) / 2 == pytest.approx(777, abs=3), "seal centred on the F|G boundary"
    assert _size(md) == pytest.approx((180, 200), abs=3)
    x0, x1, _, _ = _rect(ws, md)
    assert (x0 + x1) / 2 == pytest.approx(773, abs=3), "MD seal centred on E:G (~ the F|G boundary)"


@pytest.mark.parametrize("size", [(720, 190), (200, 400), (300, 300)])
def test_images_keep_their_aspect_ratio(size):
    ws = _ws(company=LETTERHEAD_COMPANY, logo_bytes=banner(size), seal_bytes=_png(size),
             director=MD, director_seal_bytes=_png(size))
    for img, box in zip(ws._images, [(560, 115), (170, 170), (180, 200)]):
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
    assert _size(lh)[0] == pytest.approx(560 * scale, abs=3)
    assert _size(seal) == pytest.approx((170 * scale, 170 * scale), abs=3)
    assert _size(md) == pytest.approx((180 * scale, 200 * scale), abs=3)
    x0, x1, _, _ = _rect(ws, seal)
    assert (x0 + x1) / 2 == pytest.approx(sum(col_px[:-1]), abs=3), "centred on H|I"
    for img in ws._images:
        assert _rect(ws, img)[1] <= sum(col_px), "inside A:I"


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
