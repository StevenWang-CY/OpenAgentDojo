"""boto3 wrapper for the artifact bucket.

The same client targets MinIO locally (``settings.s3_endpoint_url`` set)
and R2 / S3 in production (``s3_endpoint_url`` left unset). All helpers
operate on ``settings.s3_bucket`` so the caller doesn't have to thread
bucket names through every call site.

Operations exposed:

* :func:`get_s3_client` — build (and cache) a boto3 client.
* :func:`put_object`    — write bytes (or a binary stream) at a key,
  returning the bytes written.
* :func:`generate_download_url` — presigned GET URL, valid for
  ``expires_in`` seconds.
* :func:`delete_object` — best-effort delete; raises on transport errors
  so the caller decides whether to retry.

These are intentionally minimal — no retry policy, no multipart upload,
no listing helpers. The data-export worker writes a single zip per
request, and the deletion worker deletes a small set of keys. Anything
more elaborate should grow here in its own helper rather than threading
boto3 calls into business logic.
"""

from __future__ import annotations

import io
import logging
from functools import lru_cache
from typing import IO, Any

from app.config import get_settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_s3_client() -> Any:
    """Return a cached boto3 S3 client built from settings.

    Caching matters: boto3 client construction parses ``~/.aws/config``,
    resolves endpoints, and warms a session — non-trivial on the request
    hot path. The export worker calls this once per job; the lru_cache
    keeps subsequent calls free.
    """
    import boto3  # local import keeps cold-start cheap
    from botocore.config import Config

    settings = get_settings()
    # Bounded timeouts + retries: without these, boto3 defaults to a
    # 60s connect and indefinite-feeling read timeout, which can wedge
    # workers if R2/MinIO stalls. The retries cover transient 5xx and
    # connection resets without re-raising into business logic.
    boto_config = Config(
        connect_timeout=10,
        read_timeout=30,
        retries={"max_attempts": 3, "mode": "standard"},
    )
    kwargs: dict[str, Any] = {
        "region_name": settings.s3_region,
        "config": boto_config,
    }
    # MinIO / R2 expose a non-default endpoint; vanilla AWS S3 does not.
    if settings.s3_endpoint_url:
        kwargs["endpoint_url"] = settings.s3_endpoint_url
    if settings.s3_access_key_id and settings.s3_secret_access_key:
        kwargs["aws_access_key_id"] = settings.s3_access_key_id
        kwargs["aws_secret_access_key"] = settings.s3_secret_access_key
    return boto3.client("s3", **kwargs)


def reset_client_cache() -> None:
    """Drop the cached client — test helper for fixtures that swap settings."""
    get_s3_client.cache_clear()


def put_object(
    key: str,
    body: bytes | IO[bytes],
    *,
    content_type: str,
) -> int:
    """Write ``body`` to ``settings.s3_bucket`` at ``key``; return bytes written.

    ``body`` may be a ``bytes`` buffer or any binary stream (``BinaryIO``).
    A stream is rewound to position 0 before the upload so callers don't
    have to remember to ``seek(0)`` after assembling the payload.
    """
    settings = get_settings()
    client = get_s3_client()
    if isinstance(body, (bytes, bytearray)):
        payload = bytes(body)
        size = len(payload)
        client.put_object(
            Bucket=settings.s3_bucket,
            Key=key,
            Body=payload,
            ContentType=content_type,
        )
        return size

    # Treat anything else as a binary file-like; rewind and measure on the fly.
    body.seek(0, io.SEEK_END)
    size = body.tell()
    body.seek(0)
    client.put_object(
        Bucket=settings.s3_bucket,
        Key=key,
        Body=body,
        ContentType=content_type,
    )
    return size


def generate_download_url(key: str, *, expires_in: int) -> str:
    """Return a presigned GET URL for ``key`` valid for ``expires_in`` seconds.

    ``expires_in`` is clamped to at least 1 second — passing 0 (or a
    negative value, e.g. when the export already expired) silently
    produces a URL that's immediately stale, which is hard to debug.
    """
    settings = get_settings()
    client = get_s3_client()
    ttl = max(1, int(expires_in))
    url = client.generate_presigned_url(
        ClientMethod="get_object",
        Params={"Bucket": settings.s3_bucket, "Key": key},
        ExpiresIn=ttl,
    )
    return str(url)


def delete_object(key: str) -> None:
    """Delete ``key`` from the artifact bucket.

    Idempotent for missing keys: an S3 ``NoSuchKey``/``NotFound`` is
    swallowed (with a DEBUG log) because the deletion worker may re-run
    after a partial failure and must not 500 on the second pass. All
    other transport errors are re-raised so the caller can decide
    whether to retry or log-and-move-on.
    """
    from botocore.exceptions import ClientError

    settings = get_settings()
    client = get_s3_client()
    try:
        client.delete_object(Bucket=settings.s3_bucket, Key=key)
    except ClientError as exc:
        error_code = ""
        try:
            error_code = str(exc.response.get("Error", {}).get("Code", ""))
        except AttributeError:
            error_code = ""
        if error_code in {"NoSuchKey", "NotFound", "404"}:
            logger.debug(
                "s3_delete_object_missing",
                extra={"key": key, "error_code": error_code},
            )
            return
        raise
