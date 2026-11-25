"""UI (loop iteration 30): a Unit on products. Create a product with unit M,
bulk import a sheet with a Unit column, select the "Cable" products → Set
unit… M, then build a document from an M product and a "No" product: the
unit is prefilled, No flips to Nos live when the quantity goes 1 → 2, M stays
M, and the PDF/XLSX print the same. An older document (stored "Nos", qty 1)
still opens and shows "No". Throwaway loop-test stack only (creates data).
Skipped without Playwright. UI_SCREENSHOT_DIR saves the evidence screenshots."""
import io
import pathlib

import openpyxl
import pytest
from pypdf import PdfReader

from conftest import ADMIN_PASSWORD, ADMIN_USERNAME, destructive, unique
from test_group_move import _ok
from test_ui_group_isolation import _list_text, _login, _saved
from test_ui_select_all import SHOTS, _bar, _browser_page, _shot

sync_api = pytest.importorskip("playwright.sync_api")
pytestmark = destructive


def _pick_group(page, select, group_id):
    if page.locator(select).is_visible():
        page.select_option(select, group_id)


def _download(page, doc_id, fmt, path):
    with page.expect_download() as dl:
        page.locator(f"#documents-tbody [data-export-id='{doc_id}'][data-export-format='{fmt}']").click()
    dl.value.save_as(path)
    return path.read_bytes()


def test_product_unit_flow(admin, group_id, tmp_path):
    expect = sync_api.expect
    token = unique("unit")
    cable_cat = _ok(admin.post("/categories", json={"name": f"Cable {token}", "short_term": "U" + token[-8:].upper()}), 201)["name"]
    plain = _ok(admin.post("/products", json={"group_id": group_id, "name": f"Plug {token}"}), 201)
    old = _ok(admin.post("/documents", json={"group_id": group_id, "doc_type": "quotation", "items": [
        {"description": f"Old line {token}", "quantity": "1", "unit": "Nos", "unit_price": "3"}]}), 201)
    sheet = tmp_path / "import.xlsx"
    wb = openpyxl.Workbook()
    for row in (["Name", "Category", "Unit"], [f"Wire A {token}", cable_cat, None], [f"Wire B {token}", cable_cat, "No"],
                [f"Tape {token}", None, "roll"]):
        wb.active.append(row)
    wb.save(sheet)

    errors, doc_id = [], None
    with sync_api.sync_playwright() as p:
        page = _browser_page(p, errors)
        page.context.set_default_timeout(15000)
        _login(page, ADMIN_USERNAME, ADMIN_PASSWORD)
        _list_text(page, "products", "products-tbody", plain["sku"])

        # 1. create a product with unit M (Unit sits next to Category, near the top)
        page.click("#toggle-product-form")
        page.wait_for_selector("#product-modal.active")
        _pick_group(page, "#product-form-group", group_id)
        expect(page.locator("#product-unit")).to_have_value("No")  # the default
        page.fill("#product-name", f"Cable 2.5mm {token}")
        page.select_option("#product-unit", "M")
        _shot(page, "iter-30-product-form-unit.png")
        page.click("#product-form-submit-btn")
        _saved(page, "product-modal")
        cable = next(x for x in _ok(admin.get("/products", params={"q": token})) if x["name"].startswith("Cable 2.5mm"))
        assert cable["unit"] == "M"
        # edit shows it again; "Other…" takes free text
        page.fill("#products-filter", token)
        page.dispatch_event("#products-filter", "input")
        page.locator(f"#products-tbody [data-edit-id='{cable['id']}']").click()
        expect(page.locator("#product-unit")).to_have_value("M")
        page.click("#product-modal-close")

        # 2. bulk import with a Unit column (blank → No, "roll" → Roll)
        page.click("#open-bulk-import-btn")
        page.wait_for_selector("#bulk-import-modal.active")
        expect(page.locator("#bulk-import-modal")).to_contain_text("Unit")
        _pick_group(page, "#bulk-import-group", group_id)
        page.set_input_files("#bulk-import-excel", str(sheet))
        page.click("#bulk-import-submit-btn")
        expect(page.locator("#bulk-import-results")).to_contain_text("Created 3 products")
        page.click("#bulk-import-close")
        units = {x["name"]: x["unit"] for x in _ok(admin.get("/products", params={"q": token}))}
        assert (units[f"Wire A {token}"], units[f"Wire B {token}"], units[f"Tape {token}"]) == ("No", "No", "Roll")

        # 3. filter category Cable → Select all → Set unit… M
        page.click("#products-reset-filters-btn")
        page.select_option("#products-category-filter", cable_cat)
        expect(page.locator("#products-tbody tr")).to_have_count(2)
        page.check("#products-select-all")
        page.click("#products-set-unit-btn")
        page.wait_for_selector("#set-unit-modal.active")
        expect(page.locator("#set-unit-title")).to_have_text("Set unit for 2 products")
        page.select_option("#set-unit-select", "M")
        _shot(page, "iter-30-set-unit-bar.png")
        page.click("#set-unit-submit")
        expect(page.locator(".tick-toast.tick-ok", has_text="Unit set on 2 products")).to_be_visible()
        expect(_bar(page, "products")).to_be_hidden()  # selection cleared
        expect(page.locator("#products-tbody")).to_contain_text("Wire A")
        assert page.locator("#products-tbody tr td:nth-child(5)").all_inner_texts() == ["M", "M"]  # the Unit column

        # 4. a document from the M product and the "No" product
        page.click(".nav-item[data-view=documents]")
        page.click("#toggle-doc-form")
        page.wait_for_selector("#doc-modal.active")
        _pick_group(page, "#doc-form-group", group_id)
        page.click("#add-line-btn")
        rows = page.locator("#line-items .line-item-row")
        for i, prod in enumerate((cable, plain)):
            rows.nth(i).locator(".li-product-search").fill(f"{prod['sku']} — {prod['name']}")
            rows.nth(i).locator(".li-price").fill("10")
        expect(rows.nth(0).locator(".li-unit")).to_have_value("M")
        expect(rows.nth(1).locator(".li-unit")).to_have_value("No")
        rows.nth(1).locator(".li-qty").fill("2")
        expect(rows.nth(1).locator(".li-unit")).to_have_value("Nos")  # live
        rows.nth(0).locator(".li-qty").fill("3")
        expect(rows.nth(0).locator(".li-unit")).to_have_value("M")
        rows.nth(1).locator(".li-qty").fill("1")
        expect(rows.nth(1).locator(".li-unit")).to_have_value("No")
        rows.nth(1).locator(".li-qty").fill("2")
        _shot(page, "iter-30-document-units.png")
        doc_number = page.input_value("#doc-number")
        page.click("#doc-form-submit-btn")
        _saved(page, "doc-modal")
        row_btn = page.locator("#documents-tbody [data-view-doc-id]", has_text=doc_number)
        row_btn.wait_for()
        doc_id = row_btn.get_attribute("data-view-doc-id")
        assert [(i["unit"], float(i["quantity"])) for i in _ok(admin.get(f"/documents/{doc_id}"))["items"]] == [("M", 3), ("Nos", 2)]

        pdf_bytes = _download(page, doc_id, "pdf", tmp_path / "a.pdf")
        if SHOTS:  # rendered to iter-30-pdf-units.png for the report
            (pathlib.Path(SHOTS) / "iter-30-units.pdf").write_bytes(pdf_bytes)
        pdf = "\n".join(pg.extract_text() for pg in PdfReader(io.BytesIO(pdf_bytes)).pages)
        assert " M " in pdf.replace("\n", " ") and "Nos" in pdf, pdf
        ws = openpyxl.load_workbook(io.BytesIO(_download(page, doc_id, "xlsx", tmp_path / "a.xlsx"))).active
        units = [r[3] for r in ws.iter_rows(values_only=True) if r[0] in (1, 2) and isinstance(r[2], (int, float))]
        assert units == ["M", "Nos"], units

        # 5. the older document (stored "Nos", quantity 1) still opens: shows "No"
        page.locator(f"#documents-tbody [data-view-doc-id='{old['id']}']").click()
        page.wait_for_selector("#view-document-modal.active")
        expect(page.locator("#view-document-content")).to_contain_text(f"Old line {token}")
        cells = page.locator("#view-document-content tbody tr").first.locator("td").all_inner_texts()
        assert cells[2] == "No", cells
        page.click("#view-document-close")
        page.close()
    assert not errors, errors
    for d in (doc_id, old["id"]):
        if d:
            admin.patch(f"/documents/{d}/trash")
            admin.delete(f"/documents/{d}")
    for prod in _ok(admin.get("/products", params={"q": token})):
        admin.delete(f"/products/{prod['id']}")
