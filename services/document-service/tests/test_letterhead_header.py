"""
Company header style: "logo" (logo + seal row, unchanged) or "letterhead"
(one full-width banner image). Layout tests render the real template and
measure the box tree; the end-to-end tests go through the real HTTP API.
"""
import io
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from PIL import Image
from pypdf import PdfReader
from pydantic import ValidationError

from app import models, schemas
from app.core.config import settings
from app.core.export_pdf import generate_quotation_pdf
from app.core.export_quotation import generate_quotation_xlsx
from app.routers import companies as companies_router
from app.routers import documents as documents_router
from tests.test_quotation_visual_style import (  # noqa: F401  (fixture + helpers reused)
    COMPANY, CUSTOMER, MM, _boxes_with_class, _document, _images, _line_item, _png, _texts, layout,
)
from tests.test_supplier_position_e2e import client  # noqa: F401  (SQLite-backed API client fixture)

LETTERHEAD_COMPANY = dict(COMPANY, header_style="letterhead")


def _banner(size=(1800, 330)):
    return _png(size, "white")


# ---- PDF layout ------------------------------------------------------------

def test_letterhead_is_one_full_width_image_below_nothing_and_above_the_title(layout):
    result = layout(company=LETTERHEAD_COMPANY, logo_bytes=_banner(), seal_bytes=_png())
    first = result.pages[0]
    images = _images(first)
    assert len(images) == 1, "letterhead replaces the logo/seal row: exactly one header image (no seal)"
    img = images[0]
    table = _boxes_with_class(first, "items")[0]
    assert img.width == pytest.approx(table.width, abs=1), "banner must span the full content width"
    assert img.position_x == pytest.approx(table.position_x, abs=0.5)
    assert img.width / img.height == pytest.approx(1800 / 330, rel=0.02), "aspect ratio must be preserved"
    heading = _texts(first, "QUOTATION")[0]
    assert img.position_y + img.height <= heading.position_y, "banner overlaps the QUOTATION heading"


def test_tall_banner_is_capped_so_it_cannot_eat_the_page(layout):
    result = layout(company=LETTERHEAD_COMPANY, logo_bytes=_banner((600, 600)))
    img = _images(result.pages[0])[0]
    assert img.height <= 45 * MM + 1


def test_logo_style_is_unchanged_by_this_feature(layout):
    for company in (COMPANY, dict(COMPANY, header_style="logo"), dict(COMPANY, header_style=None)):
        images = sorted(_images(layout(company=company, logo_bytes=_png(), seal_bytes=_png()).pages[0]), key=lambda i: i.position_x)
        assert len(images) == 2 and all(i.width == pytest.approx(120, abs=0.5) for i in images)


def test_letterhead_style_without_a_banner_renders_with_no_header_image(layout):
    result = layout(company=LETTERHEAD_COMPANY)
    assert _images(result.pages[0]) == []
    assert _texts(result.pages[0], "QUOTATION")


# ---- XLSX ------------------------------------------------------------------

def _xlsx(company, logo, seal=None):
    return load_workbook(generate_quotation_xlsx(
        _document(), CUSTOMER, [(_line_item(1), None, [])], company=company, logo_bytes=logo, seal_bytes=seal))


def test_xlsx_letterhead_is_full_width_keeps_ratio_and_the_title_clears_it():
    ws = _xlsx(LETTERHEAD_COMPANY, _banner(), _png()).active
    assert len(ws._images) == 1
    ext = ws._images[0].anchor.ext
    w_px, h_px = ext.width / 9525, ext.height / 9525
    assert w_px > 900 and w_px / h_px == pytest.approx(1800 / 330, rel=0.02)
    title_row = next(c.row for row in ws.iter_rows() for c in row if c.value == "QUOTATION")
    assert (title_row - 1) * 20 >= h_px, "title row must start below the banner"


def test_xlsx_logo_style_still_has_logo_and_seal():
    ws = _xlsx(COMPANY, _png(), _png()).active
    assert len(ws._images) == 2


# ---- schemas / image handling ---------------------------------------------

def test_header_style_defaults_to_logo_and_rejects_unknown_values():
    assert schemas.CompanyCreate(name="X").header_style == "logo"
    assert schemas.CompanyUpdate(header_style="letterhead").model_dump(exclude_unset=True) == {"header_style": "letterhead"}
    with pytest.raises(ValidationError):
        schemas.CompanyCreate(name="X", header_style="banner")
    assert "header_style" in models.Company.__table__.columns and "letterhead_object_key" in models.Company.__table__.columns


def _std(size):
    buf = io.BytesIO()
    Image.new("RGB", size, "white").save(buf, "PNG")
    data, name = companies_router._standardize_letterhead(buf.getvalue(), "x.png")
    return Image.open(io.BytesIO(data)), name


def test_letterhead_upload_keeps_proportions_and_never_upscales():
    img, name = _std((1800, 330))
    assert img.size == (1800, 330) and name == "letterhead.png"       # not stretched to a square
    img, _ = _std((4000, 800))
    assert img.size == (2000, 400)                                      # downscaled, same ratio
    img, _ = _std((300, 60))
    assert img.size == (300, 60)                                        # small stays small


# ---- end to end through the HTTP API ---------------------------------------

def _png_bytes(size, color="white"):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


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


def _pdf_image_count(client, doc_id):
    r = client.get(f"/documents/{doc_id}/export/quotation-pdf")
    assert r.status_code == 200 and r.content[:4] == b"%PDF"
    return sum(len(p.images) for p in PdfReader(io.BytesIO(r.content)).pages[:1])


def test_company_style_switch_end_to_end(client, storage):
    company = _make_company(client, header_style="letterhead")
    cid = company["id"]
    assert company["header_style"] == "letterhead" and company["letterhead_object_key"] is None

    # letterhead style, banner not uploaded yet -> exports still work (no header image)
    doc = _quotation(client, cid)
    assert _pdf_image_count(client, doc) == 0

    # upload a banner and a logo + seal
    # different colours: the PDF embeds byte-identical images once, which would hide a duplicate
    for path, size, color in (("letterhead", (1800, 330), "white"), ("logo", (300, 300), "navy"), ("seal", (300, 300), "crimson")):
        r = client.post(f"/companies/{cid}/{path}", files={"file": (f"{path}.png", _png_bytes(size, color), "image/png")})
        assert r.status_code == 200, r.text
    got = client.get(f"/companies").json()
    mine = next(c for c in got if c["id"] == cid)
    assert mine["letterhead_object_key"].startswith(f"company-letterheads/{cid}/")

    assert _pdf_image_count(client, doc) == 1                      # letterhead: ONE image, no logo, no seal
    xlsx = load_workbook(io.BytesIO(client.get(f"/documents/{doc}/export/quotation").content)).active
    assert len(xlsx._images) == 1

    # switch back to logo: the banner stays saved, the header shows logo + seal
    r = client.patch(f"/companies/{cid}", json={"header_style": "logo"})
    assert r.status_code == 200 and r.json()["header_style"] == "logo" and r.json()["letterhead_object_key"]
    assert _pdf_image_count(client, doc) == 2
    assert len(load_workbook(io.BytesIO(client.get(f"/documents/{doc}/export/quotation").content)).active._images) == 2

    # and back again without re-uploading
    client.patch(f"/companies/{cid}", json={"header_style": "letterhead"})
    assert _pdf_image_count(client, doc) == 1


def test_invalid_header_style_is_rejected_by_the_api(client, storage):
    assert client.post("/companies", json={"name": "X", "header_style": "banner"}).status_code == 422
    cid = _make_company(client)["id"]
    assert client.patch(f"/companies/{cid}", json={"header_style": "banner"}).status_code == 422


def test_existing_company_without_header_style_still_exports_as_logo(client, storage):
    import uuid
    cid = _make_company(client)["id"]
    client.post(f"/companies/{cid}/logo", files={"file": ("l.png", _png_bytes((300, 300)), "image/png")})
    # simulate a row created before the column existed
    db = next(app_db())
    db.query(models.Company).filter_by(id=uuid.UUID(cid)).update({"header_style": None})
    db.commit()
    doc = _quotation(client, cid)
    assert _pdf_image_count(client, doc) == 1


def app_db():
    from app.main import app
    from app.core.db import get_db
    return app.dependency_overrides[get_db]()


# ---- migration -------------------------------------------------------------

def test_0007_migration_is_guarded_and_chained():
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0007_company_header_style_letterhead.py").read_text()
    assert 'down_revision: Union[str, None] = "0006_customer_organization"' in src
    assert src.count("not in columns") == 2, "both add_column calls must be guarded (idempotent)"
    assert len("0007_company_letterhead") <= 32
