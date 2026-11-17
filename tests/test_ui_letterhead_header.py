"""UI (loop iterations 17-18): company form "Quotation header" select with ONE
image input. The same uploaded image is used as a squared logo (Logo) or as a
proportional banner left of the seal (Letterhead banner); switching the select
needs no re-upload. Saving ticks only after the image loads back. The PDF is
checked with PyMuPDF image rects. Skipped when Playwright for Python or PyMuPDF
isn't installed. UI_SCREENSHOT_DIR saves screenshots + rendered PDF page 1."""
import os

import pytest

from conftest import ADMIN_PASSWORD, ADMIN_USERNAME, BASE_URL, unique

sync_api = pytest.importorskip("playwright.sync_api")
fitz = pytest.importorskip("pymupdf")
pytest.importorskip("PIL")
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

SITE = BASE_URL.removesuffix("/api")
SHOTS = os.environ.get("UI_SCREENSHOT_DIR")


def _banner(path, size=(720, 190)):
    """Synthetic letterhead: round logo, big name, two address lines (never the user's real logo)."""
    w, h = size
    img = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(img)
    d.ellipse((6, 6, h - 6, h - 6), outline="navy", width=5)
    d.text((h + 10, 15), "TESTCO Co., Ltd.", fill="navy", font=ImageFont.load_default(size=56))
    small = ImageFont.load_default(size=20)
    d.text((h + 10, 110), "No. 1, Sample Road, Yangon", fill="black", font=small)
    d.text((h + 10, 140), "Tel +95 1 000 000", fill="black", font=small)
    img.save(path)


def _seal(path):
    img = Image.new("RGB", (300, 300), "white")
    ImageDraw.Draw(img).ellipse((10, 10, 290, 290), outline="crimson", width=12)
    img.save(path)


def _page1(admin, company_id, shot_name):
    doc = admin.post("/documents", json={"doc_type": "quotation", "company_id": company_id, "items": []})
    assert doc.status_code == 201, doc.text
    r = admin.get(f"/documents/{doc.json()['id']}/export/quotation-pdf")
    assert r.status_code == 200 and r.content[:4] == b"%PDF"
    pdf = fitz.open(stream=r.content, filetype="pdf")
    page = pdf[0]
    # (source px size, rect on the page), left to right
    rects = sorted((((img[2], img[3]), r) for img in page.get_images(full=True) for r in page.get_image_rects(img[0])),
                   key=lambda x: x[1].x0)
    if SHOTS:
        page.get_pixmap(dpi=110).save(os.path.join(SHOTS, shot_name))
    return page, rects, doc.json()["id"]


def _shot(page, name):
    if SHOTS:
        sync_api.expect(page.locator(".tick-toast")).to_have_count(0, timeout=8000)
        page.locator("#company-header-style").evaluate("el => el.scrollIntoView({block: 'center'})")
        page.screenshot(path=os.path.join(SHOTS, name))


def _save(page):
    page.click("#company-form-submit-btn")
    sync_api.expect(page.locator(".tick-toast.tick-ok", has_text="Company saved")).to_be_visible(timeout=15000)
    page.wait_for_selector("#company-modal.active", state="hidden")


def _edit(page, company_id):
    page.click(f"[data-edit-company-id='{company_id}']")
    page.wait_for_selector("#company-modal.active")


def test_company_form_single_image_feeds_logo_and_banner(admin, tmp_path):
    name = unique("UI Letterhead Co")
    banner, seal = tmp_path / "banner.png", tmp_path / "seal.png"
    _banner(banner)
    _seal(seal)
    expect = sync_api.expect
    mm = 72 / 25.4
    docs = []
    try:
        with sync_api.sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 1000})
            try:
                page.goto(SITE + "/")
                page.fill("#login-username", ADMIN_USERNAME)
                page.fill("#login-password", ADMIN_PASSWORD)
                page.click("#login-form button[type=submit]")
                page.click(".nav-item[data-view=company]")
                page.click("#toggle-company-form")
                page.wait_for_selector("#company-modal.active")

                # one image input whatever the style; the old banner input is gone
                expect(page.locator("#company-letterhead")).to_have_count(0)
                expect(page.locator("#company-header-style")).to_have_value("logo")
                for style in ("letterhead", "logo"):
                    page.select_option("#company-header-style", style)
                    expect(page.locator("#company-logo")).to_be_visible()
                    expect(page.locator("#company-seal")).to_be_visible()
                    expect(page.locator("#company-banner-hint")).to_be_hidden()  # new company: nothing saved yet

                page.fill("#company-name", name)
                page.fill("#director-rows .director-name", "")

                # a non-image is refused before anything is saved
                bad = tmp_path / "notes.txt"
                bad.write_text("not an image")
                page.set_input_files("#company-logo", str(bad))
                page.click("#company-form-submit-btn")
                expect(page.locator("#company-modal-banner")).to_contain_text("PNG, JPG, WebP or SVG")
                expect(page.locator(".tick-toast.tick-err")).to_be_visible()
                assert name not in {c["name"] for c in admin.get("/companies").json()}

                # the wide banner goes in the single input with "Logo" selected
                page.set_input_files("#company-logo", str(banner))
                page.set_input_files("#company-seal", str(seal))
                _save(page)
                company = next(c for c in admin.get("/companies").json() if c["name"] == name)
                assert company["header_style"] == "logo" and company["logo_object_key"] and company["letterhead_object_key"]

                pdf_page, rects, doc_id = _page1(admin, company["id"], "iter-18-logo-p1.png")
                docs.append(doc_id)
                assert [size for size, _ in rects] == [(300, 300), (300, 300)], "squared logo + seal"
                (_, lg), (_, sl) = rects
                assert (lg.x0, lg.y0, lg.width, lg.height) == pytest.approx((12.7 * mm, 7.6 * mm, 90, 90), abs=1)  # 120 CSS px = 90pt
                assert sl.x1 == pytest.approx(pdf_page.rect.width - 12.7 * mm, abs=1), "seal at the right margin"

                # edit: the select switches the preview between the two saved variants
                _edit(page, company["id"])
                expect(page.locator("#company-header-style")).to_have_value("logo")
                expect(page.locator("#company-logo-preview")).to_have_js_property("naturalWidth", 300)
                _shot(page, "iter-18-company-form-logo.png")
                page.select_option("#company-header-style", "letterhead")
                expect(page.locator("#company-logo")).to_be_visible()
                expect(page.locator("#company-banner-hint")).to_be_hidden()
                expect(page.locator("#company-logo-preview")).to_have_js_property("naturalWidth", 720)
                expect(page.locator("#company-logo-preview")).to_be_visible()
                _shot(page, "iter-18-company-form-banner.png")
                _save(page)  # no new upload
                assert admin.get(f"/companies/{company['id']}").json()["header_style"] == "letterhead"

                pdf_page, rects, doc_id = _page1(admin, company["id"], "iter-18-banner-p1.png")
                docs.append(doc_id)
                assert [size for size, _ in rects] == [(720, 190), (300, 300)], "banner + seal, no squared logo"
                (_, lh), (_, sl2) = rects
                assert lh.x0 == pytest.approx(12.7 * mm, abs=1) and lh.y0 == pytest.approx(7.6 * mm, abs=1), "banner top-left"
                assert lh.x1 < sl2.x0, "banner left of the seal"
                assert lh.height <= 30 * mm + 1 and lh.width / lh.height == pytest.approx(720 / 190, rel=0.01)
                assert sl2 == sl, "seal unchanged"

                # a company whose saved image has no banner variant (here: an SVG) gets the re-upload hint
                svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>'
                r = admin.post(f"/companies/{company['id']}/logo", files={"file": ("logo.svg", svg, "image/svg+xml")})
                assert r.status_code == 200 and r.json()["letterhead_object_key"] is None
                page.reload()
                page.click(".nav-item[data-view=company]")
                _edit(page, company["id"])
                expect(page.locator("#company-header-style")).to_have_value("letterhead")
                expect(page.locator("#company-banner-hint")).to_be_visible()
                expect(page.locator("#company-banner-hint")).to_contain_text("Re-upload your banner image once")
                page.select_option("#company-header-style", "logo")
                expect(page.locator("#company-banner-hint")).to_be_hidden()
                page.click("#company-modal-close")
            finally:
                browser.close()
    finally:
        for d in docs:
            admin.delete(f"/documents/{d}")
        for c in admin.get("/companies").json():
            if c["name"] == name:
                admin.delete(f"/companies/{c['id']}")
