"""
Loop iteration 3 — quotation page margins: top 0.3in (7.6mm), left / right /
bottom 0.5in (12.7mm). "Bottom" is measured to the footer's last line
(the Customer Service footer lives in the @page bottom margin box), and
content must start right at the top margin on every page, with no hidden
extra gaps (UA h3 margins, an empty logo row, body margin).

Same approach as test_quotation_visual_style.py (whose `layout` fixture and
helpers are imported here): the HTML generate_quotation_pdf hands to
WeasyPrint is re-rendered and the box tree is measured. Sizes are CSS px.
"""
import pytest
from openpyxl import load_workbook

from app.core.export_quotation import generate_quotation_xlsx
from tests.test_quotation_visual_style import (  # noqa: F401  (layout is a fixture)
    COMPANY,
    CUSTOMER,
    DIRECTOR,
    MM,
    _document,
    _images,
    _line_item,
    _png,
    _texts,
    _walk,
    layout,
)

TOP = 7.6 * MM
SIDE = 12.7 * MM
BOTTOM = 12.7 * MM


def _visible(boxes):
    return [b for b in boxes if getattr(b, "text", None) or _images([b])]


def _split_page(page_boxes):
    """(body boxes, footer boxes) of one page: page_boxes[0] is the PageBox;
    the footer is whatever renders inside its @page margin boxes."""
    page = page_boxes[0]
    body, footer = [], []
    for child in page.children:
        (footer if type(child).__name__ == "MarginBox" else body).extend(_walk(child))
    return page, body, footer


def _with_logo(n_items=40):
    return dict(n_items=n_items, logo_bytes=_png(), seal_bytes=_png(), director_seal_bytes=_png())


def test_left_and_right_content_edges_are_12_7mm_on_every_page(layout):
    result = layout(**_with_logo())
    assert len(result.pages) >= 2
    for n, boxes in enumerate(result.pages, 1):
        page, body, footer = _split_page(boxes)
        visible = _visible(body + footer)
        left = min(b.position_x for b in visible)
        right = max(b.position_x + b.width for b in visible)
        assert left == pytest.approx(SIDE, abs=0.5 * MM), f"page {n}: left edge at {left / MM:.2f}mm"
        assert page.margin_width() - right == pytest.approx(SIDE, abs=0.5 * MM), (
            f"page {n}: right edge {(page.margin_width() - right) / MM:.2f}mm from the page edge"
        )


def test_logo_starts_at_7_6mm_from_the_top_on_every_page(layout):
    result = layout(**_with_logo())
    for n, boxes in enumerate(result.pages, 1):
        _, body, _ = _split_page(boxes)
        top_box = min(_visible(body), key=lambda b: b.position_y)
        assert _images([top_box]), f"page {n}: the topmost visible box should be the logo/seal"
        assert top_box.position_y == pytest.approx(TOP, abs=0.5 * MM), (
            f"page {n}: logo starts {top_box.position_y / MM:.2f}mm from the top"
        )


def test_footer_ends_12_7mm_from_the_bottom_and_nothing_overlaps_it(layout):
    result = layout(**_with_logo())
    for n, boxes in enumerate(result.pages, 1):
        page, body, footer = _split_page(boxes)
        phone = [b for b in footer if getattr(b, "text", None) and b.text.startswith("Phone:")]
        assert phone, f"page {n}: footer Phone line missing"
        gap = page.margin_height() - (phone[-1].position_y + phone[-1].height)
        assert gap == pytest.approx(BOTTOM, abs=1 * MM), f"page {n}: footer ends {gap / MM:.2f}mm from the bottom"
        footer_top = min(b.position_y for b in _visible(footer))
        lowest_body = max(b.position_y + b.height for b in _visible(body))
        assert lowest_body <= footer_top, (
            f"page {n}: body content ends at {lowest_body:.1f}px, below the footer's top {footer_top:.1f}px"
        )


def test_without_logo_the_title_starts_at_7_6mm(layout):
    result = layout(n_items=40)
    for n, boxes in enumerate(result.pages, 1):
        _, body, _ = _split_page(boxes)
        top_box = min(_visible(body), key=lambda b: b.position_y)
        assert top_box.text == "QUOTATION", f"page {n}: topmost box is {getattr(top_box, 'text', top_box)!r}"
        assert top_box.position_y == pytest.approx(TOP, abs=0.5 * MM), (
            f"page {n}: QUOTATION starts {top_box.position_y / MM:.2f}mm from the top"
        )


def test_two_item_quotation_is_still_one_page(layout):
    assert len(layout(**_with_logo(n_items=2)).pages) == 1


def test_xlsx_page_margins_match_the_pdf():
    ws = load_workbook(generate_quotation_xlsx(_document(), CUSTOMER, [(_line_item(1), None, [])],
                                               company=COMPANY, director=DIRECTOR)).active
    m = ws.page_margins
    assert (m.top, m.left, m.right, m.bottom) == (0.3, 0.5, 0.5, 0.5)
    assert m.header <= 0.2 and m.footer <= 0.2, "Excel's header/footer must sit inside the page margins"
    assert ws.print_title_rows, "print titles from iteration 2 must be kept"
