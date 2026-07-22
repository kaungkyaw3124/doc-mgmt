import meilisearch
from fastapi import APIRouter, Query

from app.core.config import settings

router = APIRouter(tags=["search"])

_client = meilisearch.Client(settings.meili_url, settings.meili_master_key)


@router.get("/search")
def search(
    q: str = Query(..., description="Free-text search query"),
    index: str = Query(
        "all",
        description="Which index to search: 'documents', 'products', or 'all' (default)",
    ),
    doc_type: str | None = Query(None, description="Filter documents by type: invoice/quotation/catalogue"),
    category: str | None = Query(None, description="Filter products by category"),
    limit: int = Query(20, le=100),
):
    results = {}

    if index in ("documents", "all"):
        filters = []
        if doc_type:
            filters.append(f'doc_type = "{doc_type}"')
        search_params = {"limit": limit}
        if filters:
            search_params["filter"] = " AND ".join(filters)
        try:
            doc_results = _client.index("documents").search(q, search_params)
            results["documents"] = doc_results.get("hits", [])
        except Exception as exc:
            results["documents"] = []
            results["documents_error"] = str(exc)

    if index in ("products", "all"):
        filters = []
        if category:
            filters.append(f'category = "{category}"')
        search_params = {"limit": limit}
        if filters:
            search_params["filter"] = " AND ".join(filters)
        try:
            product_results = _client.index("products").search(q, search_params)
            results["products"] = product_results.get("hits", [])
        except Exception as exc:
            results["products"] = []
            results["products_error"] = str(exc)

    return results
