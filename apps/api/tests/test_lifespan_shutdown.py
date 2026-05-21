"""Lifespan teardown must shut down the grading FS thread pool (P1-2).

``apps/api/app/grading/runner.py`` keeps a process-wide ``ThreadPoolExecutor``
so the synchronous ``forbidden_changes`` validator can drive ``driver.read_file``
without spawning a fresh event loop per call. The lifespan exit must
explicitly tear that pool down (``shutdown(wait=False, cancel_futures=True)``)
so the API process can exit cleanly and so a long-running test process
doesn't leak grading-fs worker threads across iterations.

We verify the pool's private ``_shutdown`` flag is set after the lifespan
context exits — the ``concurrent.futures`` API does not expose a public
predicate, but the attribute is stable across CPython versions.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from app import grading
from app.grading import runner
from app.main import create_app


def _swap_executor() -> ThreadPoolExecutor:
    """Install a fresh executor on the runner module and return it.

    Tests in the same session may have already shut the module-level one down
    (or are about to), so we plant a private instance for this test and
    restore the previous one in the finally block of the calling test.
    """
    new = ThreadPoolExecutor(max_workers=1, thread_name_prefix="grading-fs-test")
    runner._FS_EXECUTOR = new  # type: ignore[attr-defined]
    return new


@pytest.mark.asyncio
async def test_lifespan_shuts_down_fs_executor() -> None:
    """After the app's lifespan exits, the FS thread pool must be shut down."""
    previous = runner._FS_EXECUTOR  # type: ignore[attr-defined]
    test_executor = _swap_executor()
    try:
        app = create_app()
        async with app.router.lifespan_context(app):
            # While inside the lifespan, the executor is still alive.
            assert test_executor._shutdown is False, (
                "executor unexpectedly shut down during lifespan body"
            )

        # On lifespan exit, ``shutdown_fs_executor`` should have been invoked.
        assert test_executor._shutdown is True, (
            "expected grading FS executor to be shut down after lifespan exit"
        )
    finally:
        # Restore the original module-level executor so subsequent tests
        # can still grade. The previous one may have been a fresh, idle
        # executor or a shut-down sentinel — either way it's not the one
        # this test mutated.
        runner._FS_EXECUTOR = previous  # type: ignore[attr-defined]


def test_shutdown_fs_executor_is_idempotent() -> None:
    """Calling ``shutdown_fs_executor`` twice must not raise."""
    previous = runner._FS_EXECUTOR  # type: ignore[attr-defined]
    test_executor = _swap_executor()
    try:
        runner.shutdown_fs_executor()
        # A second call after shutdown is a no-op on ThreadPoolExecutor.
        runner.shutdown_fs_executor()
        assert test_executor._shutdown is True
    finally:
        runner._FS_EXECUTOR = previous  # type: ignore[attr-defined]


def test_runner_exposes_shutdown_fs_executor() -> None:
    """The function exists at the module level for the lifespan hook."""
    assert hasattr(runner, "shutdown_fs_executor")
    assert callable(runner.shutdown_fs_executor)
    # And it's reachable from the package root the same way the lifespan
    # imports it (``from app.grading.runner import shutdown_fs_executor``).
    assert grading.runner.shutdown_fs_executor is runner.shutdown_fs_executor
