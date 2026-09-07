import httpx

from app.core.config import settings


def get_visible_product_ids(x_allowed_projects: str | None):
    """
    Asks document-service which products are visible to the current caller
    (based on their project access), forwarding the same X-Allowed-Projects
    header value Nginx already resolved for us. Returns None if unrestricted
    (see everything), or a set of allowed product_id strings.

    If document-service can't be reached, fails CLOSED (returns an empty
    set) for a project-restricted caller — showing nothing is safer than a
    dependency hiccup silently dropping their project restriction and
    exposing every product.
    """
    if not x_allowed_projects or x_allowed_projects == "ALL":
        return None

    url = f"{settings.document_service_url}/documents/visible-product-ids"
    try:
        # document-service's gateway_auth middleware requires
        # X-Internal-Secret on every request, including this one, which
        # bypasses Nginx entirely — see app/core/gateway_auth.py there.
        response = httpx.get(
            url,
            headers={
                "X-Allowed-Projects": x_allowed_projects,
                "X-Internal-Secret": settings.internal_shared_secret,
            },
            timeout=5.0,
        )
        response.raise_for_status()
        data = response.json()
    except httpx.RequestError:
        return set()
    except httpx.HTTPStatusError:
        return set()

    if data.get("all"):
        return None
    return set(data.get("product_ids", []))
