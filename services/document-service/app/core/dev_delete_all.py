# DEV-ONLY: delete-all — this whole file goes when the delete-all buttons are
# removed (loop iteration 15). Shared by the DELETE /documents, /customers,
# /projects and /companies collection endpoints.
import logging

from fastapi import HTTPException

from app.core.config import settings

logger = logging.getLogger(__name__)


def refuse_in_production_or_view_only(x_access_level: str | None):
    """The one safety rail: never wipe the live VPS. Plus the same view-only
    rule as every single delete."""
    if settings.environment == "production":
        raise HTTPException(status_code=403, detail="delete-all is disabled in production")
    if x_access_level == "view":
        raise HTTPException(status_code=403, detail="you have view-only access")


def delete_files(keys):
    """Best-effort: the rows are already gone, so a missing or unreachable
    file is logged, never raised."""
    from app.core.storage import s3_client

    for key in filter(None, keys):
        try:
            s3_client.delete_object(Bucket=settings.minio_bucket, Key=key)
        except Exception:
            logger.exception("could not delete %s from storage", key)
