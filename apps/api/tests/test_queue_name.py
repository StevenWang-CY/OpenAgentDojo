"""Producer and consumer agree on the queue name 'provision'."""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]


def test_get_queue_uses_provision_name() -> None:
    """RQ Queue produced by the API uses the name 'provision'.

    We try the live ``get_queue()`` first: when Redis is reachable it returns a
    real Queue whose name we can read; otherwise it returns None and we fall
    back to a source-level guarantee on the literal queue name.
    """
    from app.workers.queue import get_queue

    get_queue.cache_clear()  # type: ignore[attr-defined]
    q = get_queue()
    if q is not None:
        assert q.name == "provision"

    # Source-level guarantee — read app/workers/queue.py and confirm the literal.
    queue_src = (_REPO_ROOT / "apps/api/app/workers/queue.py").read_text(encoding="utf-8")
    assert 'Queue("provision"' in queue_src, "producer queue name drifted from 'provision'"
    assert 'Queue("arena"' not in queue_src
    assert 'Queue("sandbox"' not in queue_src


def test_compose_worker_drains_provision_queue_only() -> None:
    """docker-compose worker CMD names 'provision' and NOT 'sandbox'."""
    compose = (_REPO_ROOT / "infra/compose/docker-compose.yml").read_text(encoding="utf-8")
    # We look at the worker service's command line.
    assert "rq worker" in compose
    # Must contain the provision queue.
    assert "provision" in compose
    # Must NOT have the historical "provision sandbox" trailer.
    assert "provision sandbox" not in compose


def test_sandbox_worker_dockerfile_drains_provision_queue_only() -> None:
    """The sandbox-worker image CMD names 'provision' and NOT 'sandbox'."""
    dockerfile = (_REPO_ROOT / "infra/docker/sandbox-worker.Dockerfile").read_text(encoding="utf-8")
    assert "rq worker" in dockerfile
    assert "provision" in dockerfile
    assert "provision sandbox" not in dockerfile
