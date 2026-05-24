"""S3 / R2 / MinIO storage helpers.

Thin wrapper around boto3 so the rest of the codebase doesn't have to
re-instantiate clients or handle the local-MinIO-vs-prod-R2 endpoint
divergence. Used by the P0-6 data-export worker and the
``account_deletion`` cleanup; also available to any future feature that
needs to write artifacts.
"""

from __future__ import annotations

from app.storage.s3 import (
    delete_object,
    generate_download_url,
    get_s3_client,
    put_object,
)

__all__ = [
    "delete_object",
    "generate_download_url",
    "get_s3_client",
    "put_object",
]
