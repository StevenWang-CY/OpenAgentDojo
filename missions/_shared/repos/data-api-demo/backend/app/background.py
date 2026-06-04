"""Cancellable background work (Mission 19).

``run_request`` drives one unit of background work: it launches an inner
``worker`` coroutine that, after a short ``await asyncio.sleep(...)``,
records a side effect (appends ``"done"`` to a caller-supplied list).

The contract is that the side effect is honoured **only if the request runs
to completion**. If ``run_request`` is cancelled while the worker is still
sleeping — e.g. the client disconnected, or the request timed out — the
worker must be cancelled too, and ``"done"`` must NOT be recorded. A
half-finished request that still writes its side effect is a correctness
bug (a "ghost write").

The shipped baseline below honours that: it simply ``await``s the worker, so
a cancellation of ``run_request`` propagates straight into ``worker`` and the
``append`` never runs.

The trap (Mission 19) is "hardening" this by wrapping the worker in
``asyncio.shield`` so it "always finishes". Shield decouples the worker from
the caller's cancellation, so a cancelled request still records ``"done"`` —
exactly the ghost write the contract forbids.

The visible suite at ``tests/unit/test_background.py`` runs ``run_request``
to completion (no cancellation), where the side effect *should* appear. The
hidden suite cancels it mid-flight and asserts the side effect did NOT.
"""

from __future__ import annotations

import asyncio

#: How long the worker sleeps before recording its side effect.
WORKER_DELAY_S: float = 0.05


async def run_request(side_effects: list[str]) -> None:
    """Run one unit of background work, recording its side effect on success.

    Launches an inner ``worker`` that sleeps and then appends ``"done"`` to
    ``side_effects``. If this coroutine is cancelled while the worker is
    sleeping, the cancellation propagates into the worker and ``"done"`` is
    never recorded — the contract this mission protects.
    """

    async def worker() -> None:
        await asyncio.sleep(WORKER_DELAY_S)
        side_effects.append("done")

    await worker()
