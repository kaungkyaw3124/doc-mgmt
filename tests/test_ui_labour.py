"""UI (loop iteration 23): "Include labour" on the document form. Create a
quotation with 3 lines and labour, check the live totals, save, download the
PDF and XLSX and check their totals; edit it again (checkbox and labour
prices restored); untick and save -> the exports have no labour at all.
Skipped when Playwright isn't installed. Run on the throwaway loop-test stack
only (it creates documents). UI_SCREENSHOT_DIR saves a PNG of the form."""
import io
import os

import pytest
from openpyxl import load_workbook
from pypdf import PdfReader

from conftest import ADMIN_PASSWORD, ADMIN_USERNAME, BASE_URL

sync_api = pytest.importorskip("playwright.sync_api")

SITE = BASE_URL.removesuffix("/api")
SHOTS = os.environ.get("UI_SCREENSHOT_DIR")
# (remark, qty, material price, labour price): materials 5,940,000 + 951,000 + 150,000
LINES = [("CCTV camera", "27", "220000", "20000"), ("9U rack", "1", "951000", "60000"), ("Patch cords", "1", "150000", "")]


def _download(page, doc_id, fmt, path):
    with page.expect_download() as dl:
        page.locator(f"#documents-tbody [data-export-id='{doc_id}'][data-export-format='{fmt}']").click()
    dl.value.save_as(path)
    return path.read_bytes()


def _pdf_text(data):
    return "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(data)).pages)


def _xlsx_values(data):
    ws = load_workbook(io.BytesIO(data)).active
    return [c.value for row in ws.iter_rows() for c in row if c.value is not None], ws.max_column


def test_labour_form_flow(admin, tmp_path):
    doc_id = None
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 1100}, accept_downloads=True)
        try:
            page.goto(SITE + "/")
            page.fill("#login-username", ADMIN_USERNAME)
            page.fill("#login-password", ADMIN_PASSWORD)
            page.click("#login-form button[type=submit]")
            page.wait_for_selector("#toggle-doc-form", state="visible")

            page.click("#toggle-doc-form")
            page.wait_for_selector("#doc-modal.active")
            sync_api.expect(page.locator("#doc-number")).not_to_have_value("")
            page.select_option("#doc-type", "quotation")
            page.select_option("#doc-currency", "MMK")
            assert not page.is_checked("#doc-include-labour")  # off by default
            assert not page.locator("#line-items .li-labour").first.is_visible()
            page.check("#doc-include-labour")
            for i, (remark, qty, price, labour) in enumerate(LINES):
                if i:
                    page.click("#add-line-btn")
                row = page.locator("#line-items .line-item-row").nth(i)
                row.locator(".li-remark").fill(remark)
                row.locator(".li-qty").fill(qty)
                row.locator(".li-price").fill(price)
                row.locator(".li-labour").fill(labour)
            rows = page.locator("#line-items .line-item-row")
            assert rows.nth(0).locator(".li-mat-total").inner_text() == "5940000.00"
            assert rows.nth(0).locator(".li-lab-total").inner_text() == "540000.00"
            assert rows.nth(2).locator(".li-lab-total").inner_text() == "-"
            assert page.inner_text("#doc-totals-subtotal") == "7041000.00 MMK"
            assert page.inner_text("#doc-totals-labour") == "600000.00 MMK"
            assert page.inner_text("#doc-totals-grand") == "7641000.00 MMK"
            assert page.inner_text("#doc-totals-grand-label") == "Grand Total"
            if SHOTS:
                page.locator("#doc-totals-summary").scroll_into_view_if_needed()
                page.locator("#doc-modal .modal-box").screenshot(path=os.path.join(SHOTS, "iter-23-labour-form.png"))
            doc_number = page.input_value("#doc-number")
            page.click("#doc-form-submit-btn")
            page.wait_for_selector("#doc-modal.active", state="hidden")

            row_btn = page.locator("#documents-tbody [data-view-doc-id]", has_text=doc_number)
            row_btn.wait_for()
            doc_id = row_btn.get_attribute("data-view-doc-id")
            doc = admin.get(f"/documents/{doc_id}").json()
            assert doc["include_labour"] is True and float(doc["total"]) == 7641000

            pdf = _pdf_text(_download(page, doc_id, "pdf", tmp_path / "a.pdf"))
            for s in ("SUB Total", "7,041,000.00", "600,000.00", "Grand Total", "7,641,000.00", "Labour (MMK)"):
                assert s in pdf, s
            values, cols = _xlsx_values(_download(page, doc_id, "xlsx", tmp_path / "a.xlsx"))
            assert cols == 9 and "SUB Total" in values and "Grand Total" in values and 20000 in values

            # edit: checkbox and labour prices restored
            page.locator(f"#documents-tbody [data-edit-doc-id='{doc_id}']").click()
            page.wait_for_selector("#doc-modal.active")
            assert page.is_checked("#doc-include-labour")
            assert page.locator("#line-items .li-labour").nth(1).input_value() in ("60000", "60000.00")
            assert page.inner_text("#doc-totals-grand") == "7641000.00 MMK"
            # untick -> labour hidden, totals without labour, saved without labour
            page.uncheck("#doc-include-labour")
            assert not page.locator("#line-items .li-labour").first.is_visible()
            assert page.inner_text("#doc-totals-grand") == "7041000.00 MMK"
            page.click("#doc-form-submit-btn")
            page.wait_for_selector("#doc-modal.active", state="hidden")
            doc = admin.get(f"/documents/{doc_id}").json()
            assert doc["include_labour"] is False and float(doc["total"]) == 7041000

            page.wait_for_timeout(500)  # list reload after save
            pdf = _pdf_text(_download(page, doc_id, "pdf", tmp_path / "b.pdf"))
            assert "Labour" not in pdf and "SUB Total" not in pdf and "Total:" in pdf and "7,041,000.00" in pdf
            values, cols = _xlsx_values(_download(page, doc_id, "xlsx", tmp_path / "b.xlsx"))
            assert cols == 7 and "SUB Total" not in values and "Total:" in values
        finally:
            browser.close()
            if doc_id:
                admin.patch(f"/documents/{doc_id}/trash")
                admin.delete(f"/documents/{doc_id}")
