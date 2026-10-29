"""
Duplicate-product detection, shared by create/edit/restore, bulk import,
GET /products/duplicate-check and GET /products/duplicates.

Two products are EXACT duplicates when norm(name) and norm(description)
are equal; SAME NAME when only norm(name) is; SIMILAR when the normalized
names are close (difflib ratio >= SIMILAR_RATIO). Only active (not
trashed) products are compared.

shortcut: every check loads all active products and compares in Python
(difflib for "similar"), fine up to ~10k products; past that, move the
normalized name/description into indexed columns and a trigram search.
"""
from difflib import SequenceMatcher

from app import models

SIMILAR_RATIO = 0.88
SIMILAR_LIMIT = 5


def norm(text) -> str:
    """None/blank -> ""; otherwise casefolded, stripped, inner whitespace runs collapsed to one space."""
    return " ".join(str(text).split()).casefold() if text is not None else ""


def key(name, description) -> tuple[str, str]:
    return norm(name), norm(description)


def active_products(db, exclude_id=None, visible_ids=None) -> list:
    query = db.query(models.Product).filter(models.Product.is_deleted == False)  # noqa: E712
    if exclude_id is not None:
        query = query.filter(models.Product.id != exclude_id)
    if visible_ids is not None:
        query = query.filter(models.Product.id.in_(visible_ids))
    return query.all()


def find_exact(db, name, description, exclude_id=None):
    """The first active product that is an exact duplicate, or None. Not
    filtered by visibility: uniqueness is catalogue-wide."""
    wanted = key(name, description)
    return next((p for p in active_products(db, exclude_id) if key(p.name, p.description) == wanted), None)


def check(products, name, description) -> dict:
    """Split `products` into exact / same_name / similar matches for a candidate name+description."""
    n, d = key(name, description)
    exact, same_name, similar = [], [], []
    for p in products:
        pn, pd = key(p.name, p.description)
        if pn == n:
            (exact if pd == d else same_name).append(p)
        elif n and pn:
            ratio = SequenceMatcher(None, n, pn).ratio()
            if ratio >= SIMILAR_RATIO:
                similar.append((ratio, p))
    similar.sort(key=lambda rp: rp[0], reverse=True)
    return {"exact": exact, "same_name": same_name, "similar": [p for _, p in similar[:SIMILAR_LIMIT]]}


def groups(products) -> dict:
    """Catalogue-wide duplicate groups (2+ products each), sorted by name:
    exact_groups share name+description; same_name_groups share a name but
    have 2+ different descriptions."""
    by_key, by_name = {}, {}
    for p in products:
        k = key(p.name, p.description)
        by_key.setdefault(k, []).append(p)
        by_name.setdefault(k[0], []).append(p)

    def _sorted(gs):
        return sorted((sorted(g, key=lambda p: p.sku) for g in gs), key=lambda g: (norm(g[0].name), g[0].sku))

    exact = [g for g in by_key.values() if len(g) >= 2]
    same_name = [g for g in by_name.values() if len({norm(p.description) for p in g}) >= 2]
    return {"exact_groups": _sorted(exact), "same_name_groups": _sorted(same_name)}


def brief(p) -> dict:
    return {"id": str(p.id), "sku": p.sku, "name": p.name, "description": p.description, "category": p.category}
