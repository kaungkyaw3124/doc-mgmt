import logging

import meilisearch

from app.core.config import settings

logger = logging.getLogger(__name__)

_client = meilisearch.Client(settings.meili_url, settings.meili_master_key)
_INDEX_NAME = "products"


def index_product(product) -> None:
    """
    Push a product into Meilisearch as one searchable record.
    Indexing failures are logged, not raised.
    """
    try:
        record = {
            "id": str(product.id),
            "sku": product.sku,
            "name": product.name,
            "description": product.description,
            "category": product.category,
            "unit_price": float(product.unit_price) if product.unit_price is not None else None,
            "currency": product.currency,
            "created_at": product.created_at.isoformat() if product.created_at else None,
            "search_text": " ".join(filter(None, [product.sku, product.name, product.description, product.category])),
        }

        _client.index(_INDEX_NAME).add_documents([record])
    except Exception:
        logger.exception("Failed to index product %s into Meilisearch", product.id)
