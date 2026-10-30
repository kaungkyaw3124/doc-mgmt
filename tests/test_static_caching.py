"""Loop iteration 9 (part A): the web UI is served so browsers always load
the newest version after an update: `Cache-Control: no-cache` plus a
validator on index.html and js/app.js, every security header still sent,
and index.html's <script src> carries app.js's version (its mtime, written
in by infra/nginx/static-version.js). /api responses are left alone."""
import os
import pathlib
import re
import time
from urllib.parse import urlsplit

import httpx
import pytest

from conftest import BASE_URL

SITE = BASE_URL.removesuffix("/api")
REPO = pathlib.Path(__file__).resolve().parents[1]
APP_JS = REPO / "web" / "js" / "app.js"


def _security_headers():
    """{name: value} for every `add_header` in infra/nginx/security-headers.conf."""
    text = (REPO / "infra" / "nginx" / "security-headers.conf").read_text()
    headers = dict(re.findall(r'^\s*add_header\s+(\S+)\s+"([^"]*)"', text, re.M))
    assert headers, "no add_header lines found in security-headers.conf"
    return headers


def _script_version(html):
    match = re.search(r'<script src="/js/app\.js\?v=([^"]+)"></script>', html)
    assert match, "index.html has no versioned app.js <script> tag"
    return match.group(1)


@pytest.mark.parametrize("path", ["/", "/js/app.js"])
def test_ui_files_revalidate_and_keep_every_security_header(path):
    r = httpx.get(SITE + path)
    assert r.status_code == 200, r.text[:200]
    assert "no-cache" in r.headers.get("cache-control", "")
    assert r.headers.get("etag") or r.headers.get("last-modified")
    for name, value in _security_headers().items():
        assert r.headers.get(name) == value, f"{path}: {name} missing or changed"

    again = httpx.get(SITE + path, headers={"If-None-Match": r.headers["etag"]})
    assert again.status_code == 304, f"{path}: conditional GET returned {again.status_code}"
    assert "no-cache" in again.headers.get("cache-control", "")


def test_index_is_served_with_the_current_app_js_version():
    assert "__APP_VERSION__" not in httpx.get(SITE + "/").text


@pytest.mark.skipif(urlsplit(SITE).hostname not in ("localhost", "127.0.0.1"),
                    reason="touches web/js/app.js, which only the local stack bind-mounts")
def test_script_version_changes_when_app_js_changes():
    before = httpx.get(SITE + "/")
    stat = os.stat(APP_JS)
    try:
        os.utime(APP_JS, (stat.st_atime, stat.st_mtime + 5))  # same content, newer file
        after = httpx.get(SITE + "/", headers={"If-None-Match": before.headers["etag"]})
        assert after.status_code == 200, "index.html must not be a 304 once app.js changed"
        assert _script_version(after.text) != _script_version(before.text)
    finally:
        os.utime(APP_JS, (stat.st_atime, stat.st_mtime))
    time.sleep(0.1)
    assert _script_version(httpx.get(SITE + "/").text) == _script_version(before.text)


def test_api_responses_get_no_new_cache_control():
    r = httpx.get(BASE_URL + "/auth/groups-public")
    assert r.status_code == 200, r.text
    assert "cache-control" not in r.headers
