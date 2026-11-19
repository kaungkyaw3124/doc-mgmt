"""
Moving documents, customers and projects from one group (department) to
another — superusers only (loop iteration 20).

The rule that must still hold after a move: no cross-group links. A document
and its customer and project are always in the same group (the same rule
documents.py `_check_links` enforces on save). So a move works on the
*connected set*: start from the selected rows, follow document→customer,
document→project, customer→its documents and project→its documents until
nothing new turns up. Rows already in the target group are not followed —
their links are already in the target group by the same rule.

- with_links=True  ("also move its customer and project" / "also move its
  documents"): everything in the connected set moves together.
- with_links=False: only the selection moves; refused (409) when that would
  leave a link pointing into another group.

Line items keep their snapshots and products are never moved implicitly
(they live in catalogue-service and documents only snapshot them).
"""
import logging

from fastapi import HTTPException

from app import models
from app.core.group_scope import ALL, scoped

ENTITIES = {"documents": models.Document, "customers": models.Customer, "projects": models.Project}
audit_logger = logging.getLogger("app.audit")
audit_logger.setLevel(logging.INFO)  # root is WARN (alembic.ini); its console handler still prints these


def _connected(db, rows: dict, target) -> dict:
    """rows: {entity: {id: row}} -> the same, grown to the connected set."""
    D, C, P = models.Document, models.Customer, models.Project
    while True:
        docs = [d for d in rows["documents"].values() if d.group_id != target]
        cust_ids = {d.customer_id for d in docs if d.customer_id} - rows["customers"].keys()
        proj_ids = {d.project_id for d in docs if d.project_id} - rows["projects"].keys()
        linked_by = [r.id for e in ("customers", "projects") for r in rows[e].values() if r.group_id != target]
        new_docs = []
        if linked_by:
            new_docs = (scoped(db, D, ALL)
                        .filter((D.customer_id.in_(linked_by)) | (D.project_id.in_(linked_by)))
                        .filter(D.id.notin_(list(rows["documents"])))
                        .all())
        new = {
            "customers": scoped(db, C, ALL).filter(C.id.in_(cust_ids)).all() if cust_ids else [],
            "projects": scoped(db, P, ALL).filter(P.id.in_(proj_ids)).all() if proj_ids else [],
            "documents": new_docs,
        }
        if not any(new.values()):
            return rows
        for entity, found in new.items():
            rows[entity].update({r.id: r for r in found})


def _counts(rows: dict, target) -> dict:
    return {e: sum(1 for r in rows[e].values() if r.group_id != target) for e in ENTITIES}


def describe(counts: dict) -> str:
    """{"documents": 1, "customers": 1, "projects": 0} -> "1 document, 1 customer"."""
    parts = [f"{n} {e[:-1] if n == 1 else e}" for e, n in counts.items() if n]
    return ", ".join(parts) or "nothing"


def move(db, entity: str, ids, target, with_links: bool, dry_run: bool, username: str | None):
    """Moves the rows (all or nothing, one commit). Returns (how many of each
    entity moved, the moved document rows). The caller has already checked
    superuser + target."""
    model = ENTITIES[entity]
    ids = set(ids)
    found = scoped(db, model, ALL).filter(model.id.in_(ids)).all() if ids else []
    if len(found) != len(ids):
        raise HTTPException(status_code=404, detail=f"{entity[:-1]} not found")
    selected = {e: {} for e in ENTITIES}
    selected[entity] = {r.id: r for r in found}
    rows = _connected(db, {e: dict(v) for e, v in selected.items()}, target)

    if not with_links:
        extra = {e: {i: r for i, r in rows[e].items() if i not in selected[e]} for e in ENTITIES}
        extra_counts = _counts(extra, target)
        if any(extra_counts.values()):
            tick = "also move its customer and project" if entity == "documents" else "also move its documents"
            raise HTTPException(
                status_code=409,
                detail=f"Not moved: it is linked to {describe(extra_counts)} in another group. "
                       f"Tick “{tick}” to move them together.",
            )
        rows = selected

    counts = _counts(rows, target)
    if dry_run:
        return counts, []
    moved = []
    for e in ENTITIES:
        for r in rows[e].values():
            if r.group_id != target:
                moved.append((e, r.id, r.group_id))
                r.group_id = target
    db.commit()
    for e, row_id, old in moved:
        # who / what / from / to — one line per row (docker compose logs document-service)
        audit_logger.info("group_move user=%s %s=%s from=%s to=%s", username or "unknown", e[:-1], row_id, old, target)
    moved_ids = {row_id for _, row_id, _ in moved}
    return counts, [r for r in rows["documents"].values() if r.id in moved_ids]
