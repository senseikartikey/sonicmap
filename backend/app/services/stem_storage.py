"""Private S3-compatible storage for ephemeral Stem Studio media."""
from __future__ import annotations

import threading

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.config import settings

_lock = threading.Lock()
_bucket_ready = False


def _client(endpoint: str | None = None):
    return boto3.client(
        "s3",
        endpoint_url=endpoint or settings.stem_s3_endpoint_url,
        region_name=settings.stem_s3_region,
        aws_access_key_id=settings.stem_s3_access_key,
        aws_secret_access_key=settings.stem_s3_secret_key,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def ensure_bucket() -> None:
    global _bucket_ready
    if _bucket_ready:
        return
    with _lock:
        if _bucket_ready:
            return
        client = _client()
        try:
            client.head_bucket(Bucket=settings.stem_s3_bucket)
        except ClientError:
            kwargs = {"Bucket": settings.stem_s3_bucket}
            if settings.stem_s3_region != "us-east-1":
                kwargs["CreateBucketConfiguration"] = {"LocationConstraint": settings.stem_s3_region}
            client.create_bucket(**kwargs)
        _bucket_ready = True


def presigned_upload(key: str, content_type: str, max_bytes: int) -> dict:
    ensure_bucket()
    # A signed POST policy enforces size at the object store itself. A presigned PUT only signs
    # the client's claimed metadata, allowing a modified client to upload arbitrarily many
    # bytes before the worker gets a chance to reject it.
    return _client(settings.stem_s3_public_endpoint_url).generate_presigned_post(
        Bucket=settings.stem_s3_bucket,
        Key=key,
        Fields={"Content-Type": content_type},
        Conditions=[{"Content-Type": content_type}, ["content-length-range", 1, max_bytes]],
        ExpiresIn=15 * 60,
    )


def presigned_download(key: str, filename: str | None = None) -> str:
    ensure_bucket()
    params = {"Bucket": settings.stem_s3_bucket, "Key": key}
    if filename:
        params["ResponseContentDisposition"] = f'attachment; filename="{filename}"'
    return _client(settings.stem_s3_public_endpoint_url).generate_presigned_url(
        "get_object", Params=params, ExpiresIn=15 * 60
    )


def delete_keys(keys: list[str]) -> None:
    if not keys:
        return
    ensure_bucket()
    client = _client()
    for start in range(0, len(keys), 1000):
        client.delete_objects(
            Bucket=settings.stem_s3_bucket,
            Delete={"Objects": [{"Key": key} for key in keys[start : start + 1000]], "Quiet": True},
        )


def internal_client():
    ensure_bucket()
    return _client()
