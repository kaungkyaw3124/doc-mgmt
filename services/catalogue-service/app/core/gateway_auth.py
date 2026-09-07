import hmac

from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core.config import settings

# Paths any caller can reach without the shared secret — just the
# container healthcheck, which nothing routes through Nginx.
EXEMPT_PATHS = {"/health"}


async def verify_gateway_secret(request: Request, call_next):
    """
    Rejects any request that doesn't carry the internal shared secret.

    This service trusts caller-supplied headers like X-Allowed-Projects/
    X-Access-Level/X-Has-Category-Access as already-resolved authorization
    decisions (Nginx's auth_request against auth-service computes them).
    But Nginx forwarding them is only a convention — nothing stops a
    caller with any other network path to this service (a compromised
    sibling container, a misconfigured network) from setting those
    headers directly and granting itself arbitrary access. This
    middleware is what actually enforces the trust boundary: every
    request must carry X-Internal-Secret, matching a value only Nginx
    (see infra/nginx/nginx.conf.template) and this service's sibling
    backends (see app/core/document_client.py, whose calls bypass Nginx
    entirely) are configured with. It is never sent to a browser.
    """
    if request.url.path in EXEMPT_PATHS:
        return await call_next(request)

    provided = request.headers.get("x-internal-secret", "")
    if not hmac.compare_digest(provided, settings.internal_shared_secret):
        return JSONResponse(status_code=401, content={"detail": "missing or invalid gateway credential"})

    return await call_next(request)
