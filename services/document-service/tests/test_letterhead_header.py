"""
Company header style (loop iterations 17-18): "logo" (logo + seal row, unchanged)
or "letterhead" (one wide banner filling the header box LEFT of the seal; the
seal stays at the top right). Layout tests render the real template and
measure the WeasyPrint box tree; the end-to-end tests go through the HTTP API.
Iteration 18: one /logo upload stores both the squared logo and the
proportional banner; header_style picks which one the exports use.
"""
import importlib.util
import io
import pathlib
import uuid

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from openpyxl import load_workbook
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader
from pydantic import ValidationError
from sqlalchemy import create_engine, inspect, text

from app import models, schemas
from app.core.export_quotation import generate_quotation_xlsx
from app.routers import companies as companies_router
from app.routers import documents as documents_router
from tests.test_quotation_visual_style import (  # noqa: F401  (fixture + helpers reused)
    COMPANY, CUSTOMER, MM, SIDE_MARGIN_PX, _boxes_with_class, _document, _images, _line_item, _png, _texts, layout,
)
from tests.test_supplier_position_e2e import client  # noqa: F401  (SQLite-backed API client fixture)

LETTERHEAD_COMPANY = dict(COMPANY, header_style="letterhead")
PAGE_W = 210 * MM
TOP_MARGIN = 7.6 * MM
BOX_H = 30 * MM               # the banner box height (the red rectangle the user marked)
BOX_W_WITH_SEAL = 148 * MM    # content width - seal - gap
CONTENT_W = 184.6 * MM


def banner(size=(720, 190), fmt="PNG"):
    """A synthetic letterhead: round logo, big company name, two address lines."""
    w, h = size
    img = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(img)
    r = min(w, h) // 2 - 4
    d.ellipse((4, h // 2 - r, 4 + 2 * r, h // 2 + r), outline="navy", width=4)
    font = ImageFont.load_default(size=max(10, h // 4))
    d.text((2 * r + 20, h * 0.1), "TESTCO Co., Ltd.", fill="navy", font=font)
    small = ImageFont.load_default(size=max(8, h // 10))
    d.text((2 * r + 20, h * 0.55), "No. 1, Sample Road", fill="black", font=small)
    d.text((2 * r + 20, h * 0.75), "Yangon, Myanmar", fill="black", font=small)
    buf = io.BytesIO()
    img.save(buf, fmt)
    return buf.getvalue()


def _header(result, page=0):
    boxes = result.pages[page]
    lh = [b for b in _images(boxes) if "letterhead" in (b.element.get("class") or "")]
    seal = [b for b in _images(boxes) if "seal" in (b.element.get("class") or "").split()]
    return boxes, (lh[0] if lh else None), (seal[0] if seal else None)


# ---- PDF layout ------------------------------------------------------------

@pytest.mark.parametrize("size", [(720, 190), (1600, 120), (200, 400), (100, 30)])
def test_banner_fills_the_left_box_keeps_ratio_and_the_seal_stays_right(layout, size):
    result = layout(company=LETTERHEAD_COMPANY, logo_bytes=banner(size), seal_bytes=_png())
    boxes, img, seal = _header(result)
    assert img is not None and seal is not None
    assert img.position_x == pytest.approx(SIDE_MARGIN_PX, abs=0.5), "banner starts at the left page margin"
    assert img.position_y == pytest.approx(TOP_MARGIN, abs=0.5)
    assert img.width / img.height == pytest.approx(size[0] / size[1], rel=0.01), "aspect ratio kept"
    assert img.width <= BOX_W_WITH_SEAL + 0.5 and img.height <= BOX_H + 0.5, "inside the box"
    # fits the box: limited by one side (a small banner is scaled UP too)
    assert img.width == pytest.approx(BOX_W_WITH_SEAL, abs=0.5) or img.height == pytest.approx(BOX_H, abs=0.5)
    assert img.position_x + img.width < seal.position_x, "banner must end left of the seal"
    assert seal.position_x + seal.width == pytest.approx(PAGE_W - SIDE_MARGIN_PX, abs=0.5), "seal stays at the right margin"
    assert seal.width == pytest.approx(120, abs=0.5)
    heading = _texts(boxes, "QUOTATION")[0]
    assert img.position_y + img.height <= heading.position_y, "banner overlaps the QUOTATION heading"


def test_720_banner_is_height_limited_to_30mm(layout):
    _, img, _ = _header(layout(company=LETTERHEAD_COMPANY, logo_bytes=banner(), seal_bytes=_png()))
    assert img.height == pytest.approx(BOX_H, abs=0.5)
    assert img.width == pytest.approx(BOX_H * 720 / 190, abs=0.5)


def test_without_a_seal_the_banner_may_use_the_full_width(layout):
    _, img, seal = _header(layout(company=LETTERHEAD_COMPANY, logo_bytes=banner((1600, 120))))
    assert seal is None
    assert img.width == pytest.approx(CONTENT_W, abs=0.5)
    assert img.position_x + img.width <= PAGE_W - SIDE_MARGIN_PX + 0.5, "no bleed past the right margin"


def test_header_does_not_grow_taller_than_the_logo_header(layout):
    logo = _texts(layout(company=COMPANY, logo_bytes=_png(), seal_bytes=_png()).pages[0], "QUOTATION")[0].position_y
    for size in ((720, 190), (200, 400)):
        boxes, _, _ = _header(layout(company=LETTERHEAD_COMPANY, logo_bytes=banner(size), seal_bytes=_png()))
        assert _texts(boxes, "QUOTATION")[0].position_y <= logo + 0.5, "text below the header must not move down"


def test_logo_style_renders_exactly_as_before(layout):
    # Numbers of the current layout: 120px logo at the top-left margin, 120px seal at the top-right margin.
    for company in (COMPANY, dict(COMPANY, header_style="logo"), dict(COMPANY, header_style=None)):
        result = layout(company=company, logo_bytes=_png(), seal_bytes=_png())
        logo, seal = sorted(_images(result.pages[0])[:2], key=lambda i: i.position_x)
        assert (logo.position_x, logo.position_y, logo.width, logo.height) == pytest.approx((SIDE_MARGIN_PX, TOP_MARGIN, 120, 120), abs=0.5)
        assert (seal.position_x + seal.width, seal.position_y, seal.width) == pytest.approx((PAGE_W - SIDE_MARGIN_PX, TOP_MARGIN, 120), abs=0.5)
        assert "letterhead" not in result.html.split("</style>")[1]


def test_multi_page_quotation_keeps_the_banner_in_the_box_on_every_page(layout):
    result = layout(n_items=40, company=LETTERHEAD_COMPANY, logo_bytes=banner(), seal_bytes=_png())
    assert len(result.pages) > 1
    for page in range(len(result.pages)):
        _, img, seal = _header(result, page)
        if img is not None:  # the header repeats only where it already did for the logo
            assert img.height <= BOX_H + 0.5 and img.position_x + img.width < seal.position_x


def test_letterhead_style_without_a_banner_shows_no_letterhead(layout):
    result = layout(company=LETTERHEAD_COMPANY, seal_bytes=_png())
    _, img, seal = _header(result)
    assert img is None and seal is not None
    assert _texts(result.pages[0], "QUOTATION")


# ---- XLSX ------------------------------------------------------------------

def _xlsx(company, logo, seal=None):
    return load_workbook(generate_quotation_xlsx(
        _document(), CUSTOMER, [(_line_item(1), None, [])], company=company, logo_bytes=logo, seal_bytes=seal)).active


def _size(img):
    """Displayed px size (the anchor's extent; img.width/height are the source pixels after a reload)."""
    return img.anchor.ext.width / 9525, img.anchor.ext.height / 9525


@pytest.mark.parametrize("size", [(720, 190), (1600, 120), (200, 400)])
def test_xlsx_banner_in_the_header_rows_left_of_the_seal(size):
    ws = _xlsx(LETTERHEAD_COMPANY, banner(size), _png())
    assert len(ws._images) == 2
    lh, seal = ws._images
    # Loop iteration 31: the user's drawn box — 560 x 115 px from column B's left edge.
    assert (lh.anchor._from.col, lh.anchor._from.colOff, lh.anchor._from.row) == (1, 0, 0), "banner starts at B1"
    assert (seal.anchor._from.col, seal.anchor._from.row) == (5, 0), "seal still starts in F1"
    w, h = _size(lh)
    assert w <= 561 and h <= 116, "inside the 560 x 115 box"
    assert w == pytest.approx(560, abs=1) or h == pytest.approx(115, abs=1), "fills the box"
    assert w / h == pytest.approx(size[0] / size[1], rel=0.01)
    assert 56 + w < 56 + 385 + 56 + 56 + 112 + 112 - 85, "banner ends left of the seal (seal centred on F|G)"
    title_row = next(c.row for row in ws.iter_rows() for c in row if c.value == "QUOTATION")
    assert (title_row - 1) * 20 >= max(h, _size(seal)[1]) + 5, "title row must start below the banner and seal"


def test_xlsx_logo_style_still_has_logo_and_seal_at_170px():
    ws = _xlsx(COMPANY, _png(), _png())
    assert [(i.anchor._from.col, *_size(i)) for i in ws._images] == [(0, 170, 170), (5, 170, 170)]


# ---- schemas / image handling ---------------------------------------------

def test_header_style_defaults_to_logo_and_rejects_unknown_values():
    assert schemas.CompanyCreate(name="X").header_style == "logo"
    assert schemas.CompanyUpdate(header_style="letterhead").model_dump(exclude_unset=True) == {"header_style": "letterhead"}
    with pytest.raises(ValidationError):
        schemas.CompanyCreate(name="X", header_style="banner")
    assert "header_style" in models.Company.__table__.columns and "letterhead_object_key" in models.Company.__table__.columns


def test_banner_variant_keeps_proportions_and_only_downscales():
    std = lambda data: Image.open(io.BytesIO(companies_router._standardize_letterhead(data)))
    assert std(banner((720, 190))).size == (720, 190)   # not stretched to a square
    assert std(banner((3000, 800))).size == (2000, 533)  # downscaled, same ratio
    for fmt in ("JPEG", "WEBP"):
        assert std(banner(fmt=fmt)).size == (720, 190)


@pytest.mark.parametrize("data", [b"just some text, not an image", b"GIF89a" + b"\0" * 20, b""])
def test_banner_variant_is_skipped_not_fatal_for_other_files(data):
    assert companies_router._standardize_letterhead(data) is None


# ---- end to end through the HTTP API ---------------------------------------

@pytest.fixture
def storage(monkeypatch):
    files = {}
    monkeypatch.setattr(companies_router, "upload_file", lambda fileobj, key, content_type=None: files.__setitem__(key, fileobj.read()))
    monkeypatch.setattr(documents_router, "get_file_bytes", lambda key: files[key])
    return files


def _make_company(client, **extra):
    r = client.post("/companies", json=dict({"name": "Apex Range", "short_name": "AR"}, **extra))
    assert r.status_code == 201, r.text
    return r.json()


def _quotation(client, company_id):
    r = client.post("/documents", json={"doc_type": "quotation", "company_id": company_id, "items": []})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _pdf_image_sizes(client, doc_id):
    r = client.get(f"/documents/{doc_id}/export/quotation-pdf")
    assert r.status_code == 200 and r.content[:4] == b"%PDF"
    return sorted(Image.open(io.BytesIO(i.data)).size for i in PdfReader(io.BytesIO(r.content)).pages[0].images)


def _sq(color):
    buf = io.BytesIO()
    Image.new("RGB", (300, 300), color).save(buf, "PNG")
    return buf.getvalue()


def _stored(storage, key):
    return Image.open(io.BytesIO(storage[key])).size


def _upload_logo(client, cid, data, name="logo.png", mime="image/png", **kw):
    return client.post(f"/companies/{cid}/logo", files={"file": (name, data, mime)}, **kw)


@pytest.mark.parametrize("fmt,mime", [("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp")])
def test_one_logo_upload_stores_the_square_logo_and_the_proportional_banner(client, storage, fmt, mime):
    cid = _make_company(client)["id"]
    r = _upload_logo(client, cid, banner(fmt=fmt), name="logo." + fmt.lower(), mime=mime)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["letterhead_object_key"] == f"company-letterheads/{cid}/letterhead.png" and "note" not in body
    assert _stored(storage, body["logo_object_key"]) == (300, 300)        # logo: stretched square, as before
    w, h = _stored(storage, body["letterhead_object_key"])
    assert abs((w / h) / (720 / 190) - 1) < 0.01                          # banner: proportions kept
    company = client.get(f"/companies/{cid}").json()
    assert (company["logo_object_key"], company["letterhead_object_key"]) == (body["logo_object_key"], body["letterhead_object_key"])


def test_a_very_wide_upload_gets_a_downscaled_banner(client, storage):
    cid = _make_company(client)["id"]
    key = _upload_logo(client, cid, banner((3000, 800))).json()["letterhead_object_key"]
    assert _stored(storage, key) == (2000, 533)


def test_svg_upload_stores_only_the_logo_with_a_note_and_drops_an_older_banner(client, storage):
    cid = _make_company(client)["id"]
    assert _upload_logo(client, cid, banner()).json()["letterhead_object_key"]
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>'
    r = _upload_logo(client, cid, svg, name="logo.svg", mime="image/svg+xml")
    assert r.status_code == 200, r.text
    assert r.json()["letterhead_object_key"] is None and "logo only" in r.json()["note"]
    assert storage[r.json()["logo_object_key"]] == svg                   # SVG left untouched, as before
    assert client.get(f"/companies/{cid}").json()["letterhead_object_key"] is None


@pytest.mark.parametrize("name,data", [
    ("x.txt", b"hello"),
    ("x.png", b"not really a png"),
    ("empty.png", b""),
    ("huge.png", banner() + b"\0" * (10 * 1024 * 1024)),
])
def test_logo_upload_rejects_non_images_and_huge_files(client, storage, name, data):
    cid = _make_company(client)["id"]
    r = _upload_logo(client, cid, data, name=name)
    assert r.status_code == 400, r.text
    assert ("too large" in r.json()["detail"]) == name.startswith("huge")
    assert storage == {}
    assert client.get(f"/companies/{cid}").json()["logo_object_key"] is None


def test_logo_upload_refuses_view_only(client, storage):
    cid = _make_company(client)["id"]
    assert _upload_logo(client, cid, banner(), headers={"X-Access-Level": "view"}).status_code == 403
    assert storage == {}


def test_separate_letterhead_upload_endpoint_is_gone(client, storage):
    cid = _make_company(client)["id"]
    r = client.post(f"/companies/{cid}/letterhead", files={"file": ("lh.png", banner(), "image/png")})
    assert r.status_code in (404, 405)
    assert storage == {}


def test_company_style_switch_end_to_end_from_a_single_upload(client, storage):
    company = _make_company(client, header_style="letterhead")
    cid = company["id"]
    assert company["header_style"] == "letterhead" and company["letterhead_object_key"] is None
    doc = _quotation(client, cid)
    # the seal (a different colour, so the PDF can't de-duplicate it with the logo)
    assert client.post(f"/companies/{cid}/seal", files={"file": ("seal.png", _sq("crimson"), "image/png")}).status_code == 200

    # one /logo upload of a wide image and nothing else -> banner at its own proportions + seal
    assert _upload_logo(client, cid, banner()).status_code == 200
    assert client.get(f"/companies/{cid}/letterhead-url").status_code in (200, 500)  # presign needs MinIO; route exists
    assert _pdf_image_sizes(client, doc) == [(300, 300), (720, 190)]
    xlsx = load_workbook(io.BytesIO(client.get(f"/documents/{doc}/export/quotation").content)).active
    assert len(xlsx._images) == 2 and _size(xlsx._images[0])[0] > 90

    # flip to logo: the same upload, squared, + seal
    r = client.patch(f"/companies/{cid}", json={"header_style": "logo"})
    assert r.status_code == 200 and r.json()["header_style"] == "logo" and r.json()["letterhead_object_key"]
    assert _pdf_image_sizes(client, doc) == [(300, 300), (300, 300)]

    # and back again without re-uploading
    client.patch(f"/companies/{cid}", json={"header_style": "letterhead"})
    assert _pdf_image_sizes(client, doc) == [(300, 300), (720, 190)]


def test_old_squared_logo_with_letterhead_style_falls_back_to_the_logo(client, storage):
    cid = _make_company(client, header_style="letterhead")["id"]
    _upload_logo(client, cid, banner())
    from app.core.db import get_db
    from app.main import app
    db = next(app.dependency_overrides[get_db]())
    db.query(models.Company).filter_by(id=uuid.UUID(cid)).update({"letterhead_object_key": None})  # uploaded before iteration 18
    db.commit()
    assert _pdf_image_sizes(client, _quotation(client, cid)) == [(300, 300)]


def test_invalid_header_style_is_rejected_by_the_api(client, storage):
    assert client.post("/companies", json={"name": "X", "header_style": "banner"}).status_code == 422
    cid = _make_company(client)["id"]
    assert client.patch(f"/companies/{cid}", json={"header_style": "banner"}).status_code == 422


def test_existing_company_without_header_style_still_exports_as_logo(client, storage):
    cid = _make_company(client)["id"]
    client.post(f"/companies/{cid}/logo", files={"file": ("l.png", _sq("navy"), "image/png")})
    from app.core.db import get_db
    from app.main import app
    db = next(app.dependency_overrides[get_db]())
    db.query(models.Company).filter_by(id=uuid.UUID(cid)).update({"header_style": None})  # a row from before the column
    db.commit()
    assert _pdf_image_sizes(client, _quotation(client, cid)) == [(300, 300)]


# ---- migration -------------------------------------------------------------

_VERSIONS = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions"


def test_migration_chains_after_0007_as_the_single_head():
    revs, downs = set(), set()
    for f in _VERSIONS.glob("*.py"):
        src = f.read_text()
        revs.add(src.split('revision: str = "')[1].split('"')[0])
        downs.add(src.split('down_revision: Union[str, None] = ')[1].split("\n")[0].strip('"'))
    assert len(revs - downs) == 1, "exactly one head"   # iteration 19 added 0009_group_id on top
    assert {"0007_md_position_default", "0008_company_letterhead"} <= downs and len("0008_company_letterhead") <= 32


def test_migration_upgrade_and_downgrade_on_sqlite():
    spec = importlib.util.spec_from_file_location("m0008", _VERSIONS / "0008_company_header_style_letterhead.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE companies (id INTEGER PRIMARY KEY, name VARCHAR)"))
        conn.execute(text("INSERT INTO companies (name) VALUES ('old')"))
        with Operations.context(MigrationContext.configure(conn)):
            mod.upgrade()
            mod.upgrade()  # guarded: a second run is a no-op
            cols = {c["name"] for c in inspect(conn).get_columns("companies")}
            assert {"header_style", "letterhead_object_key"} <= cols
            assert conn.execute(text("SELECT header_style FROM companies")).scalar() == "logo"
            mod.downgrade()
            assert {c["name"] for c in inspect(conn).get_columns("companies")} == {"id", "name"}
