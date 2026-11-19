"""UI (loop iteration 16): Admin -> Backup & Restore. Download a backup from
the page, add data, restore the downloaded file by typing RESTORE, and the
data is back to the backed-up state. A normal user never sees the page.

The restore test REPLACES ALL DATA, so it only runs with
ALLOW_DESTRUCTIVE_TESTS=1 on the throwaway loop-test stack. Skipped when
Playwright for Python isn't installed. UI_SCREENSHOT_DIR saves screenshots."""
import os
import tarfile

import pytest

from conftest import ADMIN_PASSWORD, ADMIN_USERNAME, BASE_URL, destructive, relogin, unique

sync_api = pytest.importorskip("playwright.sync_api")

SITE = BASE_URL.removesuffix("/api")
SHOTS = os.environ.get("UI_SCREENSHOT_DIR")


def _shot(page, name):
    if SHOTS:
        page.screenshot(path=os.path.join(SHOTS, name), full_page=True)


def _login(page, username, password):
    page.goto(SITE + "/")
    page.fill("#login-username", username)
    page.fill("#login-password", password)
    page.click("#login-form button[type=submit]")
    page.wait_for_selector("#app.active")


def test_normal_user_cannot_see_backup_page(editor_user):
    expect = sync_api.expect
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        try:
            _login(page, editor_user["username"], editor_user["password"])
            page.wait_for_function("document.getElementById('whoami-username').textContent !== '—'")
            expect(page.locator("#backup-nav-item")).to_be_hidden()
            expect(page.locator("#view-backup")).to_be_hidden()
        finally:
            browser.close()


@destructive
@pytest.mark.restores_data
def test_download_then_restore_from_the_page(admin, tmp_path, group_id):
    expect = sync_api.expect
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900}, accept_downloads=True)
        try:
            _login(page, ADMIN_USERNAME, ADMIN_PASSWORD)
            page.click("#backup-nav-item")
            expect(page.locator("#view-backup")).to_be_visible()

            with page.expect_download(timeout=300_000) as dl:
                page.click("#backup-download-btn")
            archive = tmp_path / dl.value.suggested_filename
            dl.value.save_as(archive)
            assert archive.name.startswith("backup-") and archive.name.endswith(".tar.gz")
            with tarfile.open(archive, "r:gz") as t:
                assert "manifest.json" in t.getnames()
            expect(page.locator("#backup-progress")).to_contain_text("Downloading")
            expect(page.locator("#backup-list-tbody")).to_contain_text(archive.name)
            _shot(page, "iter-16-backup-page.png")

            # data created after the backup must be gone after the restore
            extra = admin.post("/products", json={"group_id": group_id, "name": unique("Added after backup")})
            assert extra.status_code == 201, extra.text

            page.set_input_files("#restore-file", str(archive))
            page.click("#restore-check-btn")
            expect(page.locator("#restore-summary")).to_contain_text("Backup from")
            expect(page.locator("#restore-start-btn")).to_be_disabled()
            page.fill("#restore-confirm", "restore")
            expect(page.locator("#restore-start-btn")).to_be_disabled()
            page.fill("#restore-confirm", "RESTORE")
            expect(page.locator("#restore-start-btn")).to_be_enabled()
            _shot(page, "iter-16-restore-confirm.png")

            page.click("#restore-start-btn")
            expect(page.locator("#restore-result")).to_contain_text("Restore complete", timeout=300_000)
            expect(page.locator("#restore-result")).to_contain_text("pre-restore-")
            expect(page.locator(".tick-toast.tick-ok", has_text="Restore complete")).to_be_visible()
            _shot(page, "iter-16-restore-done.png")
        finally:
            browser.close()

    relogin(admin)
    assert admin.get(f"/products/{extra.json()['id']}").status_code == 404
