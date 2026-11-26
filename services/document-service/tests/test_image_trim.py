"""
Loop iteration 36: blank (transparent / near-white) margins baked into uploaded
banners and seals are trimmed at render time, so the artwork fills its box in
the PDF and the XLSX ("Regards," sits right above the visible signature).
"""
import base64
import io

import pytest
from openpyxl import load_workbook
from openpyxl.utils.units import EMU_to_pixels
from PIL import Image

from app.core.export_quotation import generate_quotation_xlsx
from app.core.image_trim import trim_blank_margins
from tests.test_letterhead_header import LETTERHEAD_COMPANY, banner
from tests.test_quotation_visual_style import (  # noqa: F401  (fixture reused)
    CUSTOMER, DIRECTOR, MM, SIDE_MARGIN_PX, _boxes_with_class, _document, _line_item, _png, _texts, layout,
)


def _img(size, box, mode="RGB", bg="white", fg="navy", fmt="PNG"):
    """size canvas in bg with a solid fg rectangle at box (x0, y0, x1, y1)."""
    img = Image.new(mode, size, bg)
    img.paste(Image.new(mode, (box[2] - box[0], box[3] - box[1]), fg), box[:2])
    buf = io.BytesIO()
    img.save(buf, fmt)
    return buf.getvalue()


def _size(data):
    with Image.open(io.BytesIO(data)) as img:
        return img.size, img.format, img.mode


def padded_banner():
    """A 600 x 190 banner with 40% white padding on the right (1000 x 190 canvas)."""
    canvas = Image.new("RGB", (1000, 190), "white")
    canvas.paste(Image.open(io.BytesIO(banner((600, 190)))), (0, 0))
    buf = io.BytesIO()
    canvas.save(buf, "PNG")
    return buf.getvalue()


def top_blank_seal():
    """A 200 x 200 transparent seal whose ink is only in the bottom 40% (60% blank on top)."""
    return _img((200, 200), (20, 120, 180, 200), mode="RGBA", bg=(0, 0, 0, 0), fg=(0, 0, 128, 255))


# ---- A1: the helper ------------------------------------------------------------

@pytest.mark.parametrize("data, expected", ids=range(8), argvalues=[
    # transparent border: content 100x50 at (50, 50), pad 2% of 200 = 4 px
    (_img((200, 200), (50, 50, 150, 100), mode="RGBA", bg=(0, 0, 0, 0), fg=(0, 0, 128, 255)), ((108, 58), "PNG", "RGBA")),
    # white border
    (_img((200, 200), (50, 50, 150, 100)), ((108, 58), "PNG", "RGB")),
    # near-white (250) border counts as blank
    (_img((200, 200), (50, 50, 150, 100), bg=(250, 250, 250)), ((108, 58), "PNG", "RGB")),
    # JPEG with white margins keeps its format (ringing around the edge adds ~6 px a side)
    (_img((300, 300), (100, 100, 200, 200), fmt="JPEG"), ((124, 124), "JPEG", "RGB")),
    # WebP too
    (_img((300, 300), (100, 100, 200, 200), fmt="WEBP"), ((112, 112), "WEBP", "RGB")),
    # big image: padding capped at 6 px
    (_img((1000, 1000), (100, 100, 900, 900)), ((812, 812), "PNG", "RGB")),
    # small image: padding at least 2 px
    (_img((60, 60), (20, 20, 40, 40)), ((24, 24), "PNG", "RGB")),
    # only one side padded: the other sides stay
    (_img((200, 100), (0, 0, 120, 100)), ((122, 100), "PNG", "RGB")),
])
def test_trims_blank_borders_keeping_format_alpha_and_padding(data, expected):
    assert _size(trim_blank_margins(data)) == expected


@pytest.mark.parametrize("data", ids=range(9), argvalues=[
    _img((200, 200), (0, 0, 200, 200)),            # no border at all
    _img((200, 200), (3, 3, 197, 197)),            # border already within padding
    _img((200, 200), (12, 12, 188, 188)),          # border within the 12 px tolerance
    _img((200, 200), (0, 0, 1, 1), fg="white"),    # all white
    _img((200, 200), (0, 0, 1, 1), mode="RGBA", bg=(0, 0, 0, 0), fg=(0, 0, 0, 0)),  # all transparent
    _img((1, 1), (0, 0, 1, 1)),                    # 1 x 1
    b"not an image",                               # corrupt
    b"\x89PNG\r\n\x1a\n" + b"\x00" * 20,           # truncated PNG
    b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>',  # SVG
])
def test_returns_the_original_bytes_when_there_is_nothing_to_trim(data):
    assert trim_blank_margins(data) == data


@pytest.mark.parametrize("data", ids=["banner", "seal", "jpeg"], argvalues=[padded_banner(), top_blank_seal(), _img((300, 300), (100, 100, 200, 200), fmt="JPEG")])
def test_idempotent(data):
    once = trim_blank_margins(data)
    assert once != data
    assert trim_blank_margins(once) == once


# ---- A2 / A3: PDF --------------------------------------------------------------

def _decoded_size(img_box):
    src = img_box.element.get("src")
    return Image.open(io.BytesIO(base64.b64decode(src.split(",", 1)[1]))).size


def test_pdf_uses_trimmed_images_and_tight_gaps(layout):
    result = layout(n_items=3, company=LETTERHEAD_COMPANY, logo_bytes=padded_banner(), seal_bytes=_png(),
                    director=DIRECTOR, director_seal_bytes=top_blank_seal())
    page = result.pages[0]
    lh, seal = _boxes_with_class(page, "letterhead")[0], _boxes_with_class(page, "seal")[0]
    w, h = _decoded_size(lh)
    assert w < 620 and h == 190, f"banner's 40% right padding is trimmed (got {w} x {h})"
    assert lh.width == pytest.approx(148 * MM, abs=0.5) or lh.height == pytest.approx(30 * MM, abs=0.5), "artwork fills the box"
    # Untrimmed, the 1000 x 190 canvas is width-limited to 148 mm and its artwork is 60% of that.
    assert lh.width > 0.6 * 148 * MM * 1.05, "visible banner artwork is bigger than before"
    assert seal.position_x + seal.width <= 793.7 - SIDE_MARGIN_PX + 0.5, "nothing overflows the page width"
    header_bottom = max(lh.position_y + lh.height, seal.position_y + seal.height)
    title = _texts(page, "QUOTATION")[0]
    assert title.position_y - header_bottom <= 6 * MM, f"header -> title gap {(title.position_y - header_bottom) / MM:.1f} mm"

    tail = next(p for p in result.pages if _boxes_with_class(p, "md-seal"))
    md = _boxes_with_class(tail, "md-seal")[0]
    assert _decoded_size(md)[1] < 100, "the seal's blank top is trimmed"
    regards = next(b for b in tail if getattr(b, "text", None) == "Regards,")
    gap = md.position_y - (regards.position_y + regards.height)
    assert 0 <= gap <= 12, f"'Regards,' -> signature gap is {gap:.1f} px"


# ---- A4: XLSX ------------------------------------------------------------------

def test_xlsx_uses_the_trimmed_aspect():
    doc = _document()
    doc.include_labour = False
    ws = load_workbook(io.BytesIO(generate_quotation_xlsx(
        doc, CUSTOMER, [(_line_item(1), None, [])], company=LETTERHEAD_COMPANY,
        logo_bytes=padded_banner(), seal_bytes=_png(), director=DIRECTOR,
        director_seal_bytes=top_blank_seal()).getvalue())).active
    lh, _seal, md = ws._images
    (tw, th), _, _ = _size(trim_blank_margins(padded_banner()))
    assert EMU_to_pixels(lh.anchor.ext.width) / EMU_to_pixels(lh.anchor.ext.height) == pytest.approx(tw / th, rel=0.02)
    assert EMU_to_pixels(md.anchor.ext.width) / EMU_to_pixels(md.anchor.ext.height) == pytest.approx(168 / 84, rel=0.03)
