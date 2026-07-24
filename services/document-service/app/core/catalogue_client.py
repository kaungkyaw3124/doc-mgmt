import uuid

import httpx

from app.core.config import settings


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
        response = httpx.get(url, timeout=5.0)
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
        response = httpx.get(url, timeout=10.0)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError:
        return []


def get_product_file_bytes(product_id: uuid.UUID) -> bytes | None:
    """The product's own catalogue file, raw bytes. None if it has none, or
    catalogue-service couldn't be reached."""
    url = f"{settings.catalogue_service_url}/products/{product_id}/file-content"
    try:
        response = httpx.get(url, timeout=20.0)
        if response.status_code != 200:
            return None
        return response.content
    except httpx.RequestError:
        return None


def get_product_download_bundle_bytes(product_id: uuid.UUID) -> bytes | None:
    """The zip of a product's sub-items' catalogue files (same one the
    product's own "View file" button downloads). None if there's nothing
    to bundle, or catalogue-service couldn't be reached."""
    url = f"{settings.catalogue_service_url}/products/{product_id}/download-bundle"
    try:
        response = httpx.get(url, timeout=30.0)
        if response.status_code != 200:
            return None
        return response.content
    except httpx.RequestError:
        return None
