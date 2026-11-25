"""
Duplicate-product detection, shared by create/edit/restore, bulk import,
GET /products/duplicate-check and GET /products/duplicates.

Duplicates are looked for within one group (department) only.
Two products are EXACT duplicates when norm(name) and norm(description)
are equal; SAME NAME when only norm(name) is; SIMILAR when the normalized
names are close (difflib ratio >= SIMILAR_RATIO). Only active (not
trashed) products are compared.

shortcut: every check loads all active products and compares in Python
(difflib for "similar"), fine up to ~10k products; past that, move the
normalized name/description into indexed columns and a trigram search.
"""
import hashlib
from difflib import SequenceMatcher
from itertools import combinations

from app import models
from app.core.group_scope import ALL, scoped

SIMILAR_RATIO = 0.88
SIMILAR_LIMIT = 5


def norm(text) -> str:
    """None/blank -> ""; otherwise casefolded, stripped, inner whitespace runs collapsed to one space."""
    return " ".join(str(text).split()).casefold() if text is not None else ""


def key(name, description) -> tuple[str, str]:
    return norm(name), norm(description)


_ANY_GROUP = object()


def active_products(db, scope, exclude_id=None, visible_ids=None, group_id=_ANY_GROUP) -> list:
    """Active products the caller (`scope`) can see; group_id limits them to one group."""
    query = scoped(db, models.Product, scope).filter(models.Product.is_deleted == False)  # noqa: E712
    if group_id is not _ANY_GROUP:
        query = query.filter(models.Product.group_id == group_id if group_id else models.Product.group_id.is_(None))
    if exclude_id is not None:
        query = query.filter(models.Product.id != exclude_id)
    if visible_ids is not None:
        query = query.filter(models.Product.id.in_(visible_ids))
    return query.all()


def find_exact(db, name, description, group_id, exclude_id=None):
    """The first active product of `group_id` that is an exact duplicate, or
    None. Not filtered by the caller's visibility: uniqueness is per group."""
    wanted = key(name, description)
    return next(
        (p for p in active_products(db, ALL, exclude_id, group_id=group_id) if key(p.name, p.description) == wanted),
        None,
    )


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


def fingerprint(p) -> str:
    """What a "Not a duplicate" dismissal remembers about a product: editing its name or description changes it."""
    n, d = key(p.name, p.description)
    return hashlib.sha256(f"{n}|{d}".encode()).hexdigest()


def pair(a, b) -> tuple:
    """(a, b) in stored order: the smaller id (as a string) first."""
    return (a, b) if str(a.id) < str(b.id) else (b, a)


def effective_dismissals(db, scope, products) -> dict:
    """{(id_a, id_b): dismissal} for dismissals whose products are both in
    `products`, in the same group, and unchanged since they were dismissed."""
    by_id = {p.id: p for p in products}
    out = {}
    for d in scoped(db, models.ProductDuplicateDismissal, scope):
        a, b = by_id.get(d.product_a_id), by_id.get(d.product_b_id)
        if a and b and a.group_id == b.group_id and (fingerprint(a), fingerprint(b)) == (d.fingerprint_a, d.fingerprint_b):
            out[(a.id, b.id)] = d
    return out


def groups(products, dismissed=frozenset()) -> dict:
    """Catalogue-wide duplicate groups (2+ products each), sorted by name:
    exact_groups share name+description; same_name_groups share a name but
    have 2+ different descriptions. A same-name group whose every
    different-description pair is in `dismissed` ((id_a, id_b) in pair()
    order) is left out — and counted in hidden_count. One undismissed pair
    (e.g. a product added later) shows the whole group again."""
    by_key, by_name = {}, {}
    for p in products:
        k = key(p.name, p.description)
        by_key.setdefault((p.group_id, k), []).append(p)  # per group: two departments may sell the same item
        by_name.setdefault((p.group_id, k[0]), []).append(p)

    def _sorted(gs):
        return sorted((sorted(g, key=lambda p: p.sku) for g in gs), key=lambda g: (norm(g[0].name), g[0].sku))

    def _undismissed(g):
        return any(norm(a.description) != norm(b.description) and tuple(x.id for x in pair(a, b)) not in dismissed
                   for a, b in combinations(g, 2))

    exact = [g for g in by_key.values() if len(g) >= 2]
    same_name = [g for g in by_name.values() if len({norm(p.description) for p in g}) >= 2]
    shown = [g for g in same_name if _undismissed(g)]
    return {"exact_groups": _sorted(exact), "same_name_groups": _sorted(shown), "hidden_count": len(same_name) - len(shown)}


def brief(p) -> dict:
    return {"id": str(p.id), "sku": p.sku, "name": p.name, "description": p.description, "category": p.category}
