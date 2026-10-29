"""
Each backend reachable and answering through the gateway.

The services' own GET /health routes are not proxied by Nginx (docs/api.md
"Health checks"; scripts/verify.sh probes those from inside the nginx
container), so "healthy via the gateway" here means: the cheapest real
route Nginx forwards to that service returns 200. A stopped service shows
up as a 502/504 from Nginx and fails the matching case by name.
"""
import httpx
import pytest

from conftest import BASE_URL, auth_headers

# service -> (path under /api, needs a token)
GATEWAY_ROUTES = {
    "auth-service": ("/auth/groups-public", False),
    "document-service": ("/documents", True),
    "catalogue-service": ("/categories", True),
    "search-service": ("/search?q=healthcheck&index=documents", True),
}


@pytest.mark.parametrize("service", list(GATEWAY_ROUTES))
def test_service_reachable_via_gateway(service, client, request):
    path, needs_token = GATEWAY_ROUTES[service]
    headers = auth_headers(request.getfixturevalue("admin_token")) if needs_token else {}
    resp = client.get(path, headers=headers)
    assert resp.status_code == 200, (
        f"{service} not healthy via gateway: GET {BASE_URL}{path} -> {resp.status_code} {resp.text[:200]}"
    )


def test_web_ui_served():
    root = BASE_URL.rsplit("/api", 1)[0] + "/"
    resp = httpx.get(root, timeout=30.0)
    assert resp.status_code == 200
    assert "<html" in resp.text.lower()
