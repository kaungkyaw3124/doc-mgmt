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

# Separate client used ONLY for generating presigned URLs, pointed at the
# publicly-reachable endpoint (e.g. localhost:9000) rather than the internal
# Docker network hostname (e.g. minio:9000). Browsers/host machines can't
# resolve Docker service names, so URLs meant to be opened outside the
# Docker network must be signed against a host they can actually reach.
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
    file types (like SVG) that can carry executable content and shouldn't
    be opened directly in the browser's origin.
    """
    params = {"Bucket": settings.minio_bucket, "Key": object_key}
    if force_download:
        params["ResponseContentDisposition"] = "attachment"
    return _public_s3_client.generate_presigned_url(
        "get_object",
        Params=params,
        ExpiresIn=expires_in,
    )


def get_file_bytes(object_key: str) -> bytes:
    """Fetch a file's raw content directly (used e.g. to embed a logo image
    into a generated document, rather than linking to it)."""
    response = s3_client.get_object(Bucket=settings.minio_bucket, Key=object_key)
    return response["Body"].read()
