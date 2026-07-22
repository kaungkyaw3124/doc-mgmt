import logging

import meilisearch

from app.core.config import settings

logger = logging.getLogger(__name__)

_client = meilisearch.Client(settings.meili_url, settings.meili_master_key)
_INDEX_NAME = "documents"


def index_document(doc) -> None:
    """
    Push a document (with its items loaded) into Meilisearch as one searchable
    record. Indexing failures are logged, not raised — a search-index hiccup
    should never block someone from creating a document.
    """
    try:
        product_names = [item.description for item in doc.items]

        project_id = str(doc.project_id) if doc.project_id else None
        project_name = doc.project.name if doc.project else None
        project_budget_year = doc.project.budget_year if doc.project else None

        search_text_parts = [doc.doc_number, doc.doc_type] + product_names
        if project_name:
            search_text_parts.append(project_name)

        record = {
            "id": str(doc.id),
            "doc_type": doc.doc_type,
            "doc_number": doc.doc_number,
            "customer_id": str(doc.customer_id) if doc.customer_id else None,
            "project_id": project_id,
            "project_name": project_name,
            "project_budget_year": project_budget_year,
            "status": doc.status,
            "currency": doc.currency,
            "total": float(doc.total) if doc.total is not None else None,
            "issue_date": doc.issue_date.isoformat() if doc.issue_date else None,
            "created_at": doc.created_at.isoformat() if doc.created_at else None,
            "item_descriptions": product_names,
            "search_text": " ".join(search_text_parts),
        }

        _client.index(_INDEX_NAME).add_documents([record], primary_key="id")
    except Exception:
        logger.exception("Failed to index document %s into Meilisearch", doc.id)


def remove_document_from_index(doc_id) -> None:
    """Removes a document from the search index — call this on delete, or
    it'll keep showing up as a stale "ghost" result forever."""
    try:
        _client.index(_INDEX_NAME).delete_document(str(doc_id))
    except Exception:
        logger.exception("Failed to remove document %s from Meilisearch", doc_id)
