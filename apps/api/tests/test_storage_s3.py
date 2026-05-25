"""Unit tests for the boto3 S3 wrapper.

Covers:

* P0-1 — ``get_s3_client`` configures bounded connect/read timeouts and
  retries so a stalled R2/MinIO does not wedge a worker indefinitely.
* P1-1 — ``delete_object`` is idempotent for ``NoSuchKey``/``NotFound``
  but re-raises every other ``ClientError`` so the caller can decide
  whether to retry.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from app.storage import s3 as s3_module


@pytest.fixture(autouse=True)
def _clear_client_cache():
    """Reset the lru_cache so each test gets a fresh client construction."""
    s3_module.reset_client_cache()
    yield
    s3_module.reset_client_cache()


def test_get_s3_client_configures_bounded_timeouts():
    """P0-1: connect/read timeouts and retry budget are pinned."""
    client = s3_module.get_s3_client()
    config = client.meta.config
    assert config.connect_timeout == 10
    assert config.read_timeout == 30
    # boto3 normalises ``max_attempts=3`` into ``total_max_attempts=4``
    # (initial attempt + 3 retries) on the resolved Config; the mode is
    # preserved verbatim.
    retries = config.retries or {}
    assert retries.get("mode") == "standard"
    assert retries.get("total_max_attempts") == 4


def test_delete_object_swallows_no_such_key():
    """P1-1: a NoSuchKey response is treated as idempotent success."""
    fake_client = MagicMock()
    fake_client.delete_object.side_effect = ClientError(
        error_response={"Error": {"Code": "NoSuchKey", "Message": "missing"}},
        operation_name="DeleteObject",
    )
    with patch.object(s3_module, "get_s3_client", return_value=fake_client):
        # Should NOT raise.
        s3_module.delete_object("missing/key.zip")
    fake_client.delete_object.assert_called_once()


def test_delete_object_swallows_not_found():
    """P1-1: a NotFound (404) is treated the same as NoSuchKey."""
    fake_client = MagicMock()
    fake_client.delete_object.side_effect = ClientError(
        error_response={"Error": {"Code": "NotFound", "Message": "404"}},
        operation_name="DeleteObject",
    )
    with patch.object(s3_module, "get_s3_client", return_value=fake_client):
        s3_module.delete_object("missing/key.zip")


def test_delete_object_reraises_transport_errors():
    """P1-1: a 500-style error is surfaced for caller-side retry."""
    fake_client = MagicMock()
    fake_client.delete_object.side_effect = ClientError(
        error_response={"Error": {"Code": "InternalError", "Message": "boom"}},
        operation_name="DeleteObject",
    )
    with patch.object(s3_module, "get_s3_client", return_value=fake_client):
        with pytest.raises(ClientError):
            s3_module.delete_object("some/key.zip")
