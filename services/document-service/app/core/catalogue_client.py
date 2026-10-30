import logging
import uuid

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# catalogue-service's gateway_auth middleware requires this on every
# request, including ones (like these) that bypass Nginx entirely —
# see app/core/gateway_auth.py for why.
_INTERNAL_HEADERS = {"X-Internal-Secret": settings.internal_shared_secret}


class ProductNotFoundError(Exception):
    pass


class CatalogueServiceUnavailableError(Exception):
    pass


def get_product(product_id: uuid.UUID) -> dict:
    """
    Calls catalogue-service to fetch a product by id.
    Raises ProductNotFoundError if the product doesn't exist,
    or CatalogueServiceUnavailableError if catalogue-service can't be reached.
    """
    url = f"{settings.catalogue_service_url}/products/{product_id}"
    try:
        response = httpx.get(url, headers=_INTERNAL_HEADERS, timeout=5.0)
    except httpx.RequestError as exc:
        raise CatalogueServiceUnavailableError(
            f"could not reach catalogue-service at {settings.catalogue_service_url}: {exc}"
        )

    if response.status_code == 404:
        raise ProductNotFoundError(f"product {product_id} not found in catalogue")

    response.raise_for_status()
    return response.json()


def get_product_sub_items(product_id: uuid.UUID) -> list:
    """Returns [] if catalogue-service can't be reached or the product has
    none — fails open rather than blocking the whole catalogue export over
    one product's sub-item lookup."""
    url = f"{settings.catalogue_service_url}/products/{product_id}/sub-items"
    try:
        response = httpx.get(url, headers=_INTERNAL_HEADERS, timeout=10.0)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as exc:
        logger.warning("get_product_sub_items(%s) failed: %s", product_id, exc)
        return []


def get_product_file_bytes(product_id: uuid.UUID) -> bytes | None:
    """The product's own catalogue file, raw bytes. None if it has none, or
    catalogue-service couldn't be reached."""
    url = f"{settings.catalogue_service_url}/products/{product_id}/file-content"
    try:
        response = httpx.get(url, headers=_INTERNAL_HEADERS, timeout=45.0)
        if response.status_code != 200:
            logger.warning(
                "get_product_file_bytes(%s) got HTTP %s from %s", product_id, response.status_code, url
            )
            return None
        return response.content
    except httpx.RequestError as exc:
        logger.warning("get_product_file_bytes(%s) failed: %s", product_id, exc)
        return None


def get_product_download_bundle_bytes(product_id: uuid.UUID) -> bytes | None:
    """The zip of a product's sub-items' catalogue files (same one the
    product's own "View file" button downloads). None if there's nothing
    to bundle, or catalogue-service couldn't be reached.

    Generous timeout: this endpoint fetches every sub-item's file from
    storage and zips them, and has been observed taking 30+ seconds even
    for just a handful of small files (worth investigating separately —
    likely MinIO/boto3 connection overhead — but for now this needs
    enough headroom to actually finish rather than get cut off early)."""
    url = f"{settings.catalogue_service_url}/products/{product_id}/download-bundle"
    try:
        response = httpx.get(url, headers=_INTERNAL_HEADERS, timeout=90.0)
        if response.status_code != 200:
            logger.warning(
                "get_product_download_bundle_bytes(%s) got HTTP %s from %s: %s",
                product_id, response.status_code, url, response.text[:300],
            )
            return None
        return response.content
    except httpx.RequestError as exc:
        logger.warning("get_product_download_bundle_bytes(%s) failed: %s", product_id, exc)
        return None
