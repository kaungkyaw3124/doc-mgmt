"""UI (loop iteration 22): create an Invoice in the New document form, download
its PDF from the Documents list, and check page 1 says INVOICE / "Invoice ID"
(not QUOTATION) and the file is named Invoice-<number>.pdf. Skipped when
Playwright isn't installed. Set UI_SCREENSHOT_DIR to also save a PNG of the
PDF's first page (needs PyMuPDF)."""
import io
import os

import pytest
from pypdf import PdfReader

from conftest import ADMIN_PASSWORD, ADMIN_USERNAME, BASE_URL

sync_api = pytest.importorskip("playwright.sync_api")

SITE = BASE_URL.removesuffix("/api")
SHOTS = os.environ.get("UI_SCREENSHOT_DIR")


def test_invoice_pdf_says_invoice(admin, tmp_path):
    doc_id = None
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 1000}, accept_downloads=True)
        try:
            page.goto(SITE + "/")
            page.fill("#login-username", ADMIN_USERNAME)
            page.fill("#login-password", ADMIN_PASSWORD)
            page.click("#login-form button[type=submit]")
            page.wait_for_selector("#toggle-doc-form", state="visible")

            page.click("#toggle-doc-form")
            page.wait_for_selector("#doc-modal.active")
            sync_api.expect(page.locator("#doc-number")).not_to_have_value("")
            page.select_option("#doc-type", "invoice")
            page.fill("#line-items .li-remark", "UI invoice item")  # a non-product line needs a remark
            page.fill("#line-items .li-qty", "1")
            page.fill("#line-items .li-price", "5")
            doc_number = page.input_value("#doc-number")
            page.click("#doc-form-submit-btn")
            page.wait_for_selector("#doc-modal.active", state="hidden")

            row_btn = page.locator("#documents-tbody [data-view-doc-id]", has_text=doc_number)
            row_btn.wait_for()
            doc_id = row_btn.get_attribute("data-view-doc-id")
            pdf_btn = page.locator(f"#documents-tbody [data-export-id='{doc_id}'][data-export-format='pdf']")
            with page.expect_download() as dl:
                pdf_btn.click()
            download = dl.value
            # the browser replaces "/" (in numbers like DOC-20261118/001) with "_" when saving
            assert download.suggested_filename == f"Invoice-{doc_number}.pdf".replace("/", "_")
            path = tmp_path / "invoice.pdf"
            download.save_as(path)
        finally:
            browser.close()
            if doc_id:
                admin.patch(f"/documents/{doc_id}/trash")
                admin.delete(f"/documents/{doc_id}")

    text = PdfReader(io.BytesIO(path.read_bytes())).pages[0].extract_text()
    assert "INVOICE" in text and "Invoice ID" in text
    assert "QUOTATION" not in text and "Quotation ID" not in text
    if SHOTS:
        fitz = pytest.importorskip("fitz")
        fitz.open(path)[0].get_pixmap(dpi=90).save(os.path.join(SHOTS, "iter-22-invoice-pdf-p1.png"))
