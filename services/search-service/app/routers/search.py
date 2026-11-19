import uuid

import meilisearch
from fastapi import APIRouter, Header, Query

from app.core.config import settings
from app.core.document_client import get_visible_product_ids

router = APIRouter(tags=["search"])

_client = meilisearch.Client(settings.meili_url, settings.meili_master_key)


def _escape_filter_value(value: str) -> str:
    """Escapes backslashes and double-quotes so a query param can't break
    out of its quoted string in a Meilisearch filter expression and add
    arbitrary filter conditions."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _parse_allowed_projects(x_allowed_projects: str | None):
    """Returns None (no restriction) or a set of allowed project_id
    strings — mirrors document-service's own helper of the same name."""
    if not x_allowed_projects or x_allowed_projects == "ALL":
        return None
    if x_allowed_projects == "NONE":
        return set()
    return set(x_allowed_projects.split(","))


def _group_filter(x_group_ids: str | None, x_is_superuser: str | None):
    """Department separation (see document-service app/core/group_scope.py):
    None = no filter (superuser), "" = caller is in no group (search nothing),
    else a Meilisearch filter on the indexed group_id. Missing headers fail
    closed (treated as "no group")."""
    if x_is_superuser == "1":
        return None
    ids = []
    for part in (x_group_ids or "").split(","):
        try:
            ids.append(str(uuid.UUID(part.strip())))
        except ValueError:
            continue
    if not ids:
        return ""
    return "group_id IN [" + ", ".join(f'"{i}"' for i in ids) + "]"


@router.get("/search")
def search(
    q: str = Query(..., description="Free-text search query"),
    index: str = Query(
        "all",
        description="Which index to search: 'documents', 'products', or 'all' (default)",
    ),
    doc_type: str | None = Query(None, description="Filter documents by type: invoice/quotation/catalogue"),
    project_id: str | None = Query(None, description="Filter documents by linked project"),
    category: str | None = Query(None, description="Filter products by category"),
    limit: int = Query(20, le=100),
    x_allowed_projects: str | None = Header(default=None, alias="X-Allowed-Projects"),
    x_group_ids: str | None = Header(default=None, alias="X-Group-Ids"),
    x_is_superuser: str | None = Header(default=None, alias="X-Is-Superuser"),
):
    results = {}
    allowed_projects = _parse_allowed_projects(x_allowed_projects)
    group_filter = _group_filter(x_group_ids, x_is_superuser)
    if group_filter == "":
        return {"documents": [], "products": []} if index == "all" else {index: []}

    if index in ("documents", "all"):
        if allowed_projects is not None and not allowed_projects:
            # X-Allowed-Projects: NONE — restricted to zero projects, so
            # there's nothing to search rather than calling Meilisearch.
            results["documents"] = []
        else:
            filters = [group_filter] if group_filter else []
            if doc_type:
                filters.append(f'doc_type = "{_escape_filter_value(doc_type)}"')
            if project_id:
                filters.append(f'project_id = "{_escape_filter_value(project_id)}"')
            if allowed_projects is not None:
                ids = ", ".join(f'"{_escape_filter_value(pid)}"' for pid in allowed_projects)
                # Documents with no project stay visible to everyone — same
                # rule document-service's own project filtering uses.
                filters.append(f"(project_id IN [{ids}] OR project_id IS NULL)")
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
        visible_product_ids = get_visible_product_ids(x_allowed_projects)
        if visible_product_ids is not None and not visible_product_ids:
            results["products"] = []
        else:
            filters = [group_filter] if group_filter else []
            if category:
                filters.append(f'category = "{_escape_filter_value(category)}"')
            # Over-fetch when project-restricted, since results are
            # filtered by visibility after the Meilisearch query — without
            # this, a restricted caller could get fewer than `limit` hits
            # even though more visible matches exist further down.
            fetch_limit = limit if visible_product_ids is None else min(max(limit * 5, limit), 200)
            search_params = {"limit": fetch_limit}
            if filters:
                search_params["filter"] = " AND ".join(filters)
            try:
                product_results = _client.index("products").search(q, search_params)
                hits = product_results.get("hits", [])
                if visible_product_ids is not None:
                    hits = [h for h in hits if h.get("id") in visible_product_ids]
                results["products"] = hits[:limit]
            except Exception as exc:
                results["products"] = []
                results["products_error"] = str(exc)

    return results
