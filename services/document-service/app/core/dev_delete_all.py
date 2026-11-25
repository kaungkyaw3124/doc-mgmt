# DEV-ONLY: delete-all — this whole file goes when the delete-all buttons are
# removed (loop iteration 15). Shared by the DELETE /documents, /customers,
# /projects and /companies collection endpoints.
import logging
import uuid

from fastapi import HTTPException

from app.core.config import settings
from app.core.group_scope import require_superuser

logger = logging.getLogger(__name__)


def refuse_in_production_or_view_only(x_access_level: str | None, scope):
    """The one safety rail: never wipe the live VPS. Plus the same view-only
    rule as every single delete, and superusers only (iteration 29: a group
    editor used to be able to wipe their whole group with one click)."""
    if settings.environment == "production":
        raise HTTPException(status_code=403, detail="delete-all is disabled in production")
    require_superuser(scope)
    if x_access_level == "view":
        raise HTTPException(status_code=403, detail="you have view-only access")


def only_group(query, model, group_id: str | None):
    """?group_id=<uuid>|none: limit a wipe to one group ("none" = unassigned
    rows); no parameter = every group. A malformed id is a 422, never "all"."""
    if group_id is None:
        return query
    if group_id == "none":
        return query.filter(model.group_id == None)  # noqa: E711
    try:
        gid = uuid.UUID(group_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="group_id must be a group id or 'none'")
    return query.filter(model.group_id == gid)


def delete_files(keys):
    """Best-effort: the rows are already gone, so a missing or unreachable
    file is logged, never raised."""
    from app.core.storage import s3_client

    for key in filter(None, keys):
        try:
            s3_client.delete_object(Bucket=settings.minio_bucket, Key=key)
        except Exception:
            logger.exception("could not delete %s from storage", key)
