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
