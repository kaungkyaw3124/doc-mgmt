import boto3
from botocore.client import Config as BotoConfig

from app.core.config import settings

_protocol = "https" if settings.minio_secure else "http"

s3_client = boto3.client(
    "s3",
    endpoint_url=f"{_protocol}://{settings.minio_endpoint}",
    aws_access_key_id=settings.minio_access_key,
    aws_secret_access_key=settings.minio_secret_key,
    config=BotoConfig(signature_version="s3v4"),
)

# Separate client for presigned URLs, pointed at the publicly-reachable
# endpoint rather than the internal Docker hostname — see document-service's
# storage.py for the full explanation of why this split exists.
_public_s3_client = boto3.client(
    "s3",
    endpoint_url=f"{_protocol}://{settings.minio_public_endpoint}",
    aws_access_key_id=settings.minio_access_key,
    aws_secret_access_key=settings.minio_secret_key,
    config=BotoConfig(signature_version="s3v4"),
)


def ensure_bucket_exists():
    existing = [b["Name"] for b in s3_client.list_buckets().get("Buckets", [])]
    if settings.minio_bucket not in existing:
        s3_client.create_bucket(Bucket=settings.minio_bucket)


def upload_file(file_obj, object_key: str, content_type: str = "application/octet-stream"):
    s3_client.upload_fileobj(
        file_obj,
        settings.minio_bucket,
        object_key,
        ExtraArgs={"ContentType": content_type},
    )
    return object_key


def get_presigned_url(object_key: str, expires_in: int = 3600, force_download: bool = False) -> str:
    """
    force_download=True adds a Content-Disposition: attachment override, so
    the browser saves the file instead of rendering it inline — used for
    file types (like SVG/HTML) that can carry executable content and
    shouldn't be opened directly in the browser's origin. See
    app/core/upload_safety.py for what decides this.
    """
    params = {"Bucket": settings.minio_bucket, "Key": object_key}
    if force_download:
        params["ResponseContentDisposition"] = "attachment"
    return _public_s3_client.generate_presigned_url(
        "get_object",
        Params=params,
        ExpiresIn=expires_in,
    )


def download_file_bytes(object_key: str) -> bytes:
    """Fetches a stored file's raw bytes — used to bundle sub-item catalogue
    files into a zip server-side (a presigned URL doesn't help there, since
    we need the actual content to rename+compress, not just a link)."""
    response = s3_client.get_object(Bucket=settings.minio_bucket, Key=object_key)
    return response["Body"].read()
