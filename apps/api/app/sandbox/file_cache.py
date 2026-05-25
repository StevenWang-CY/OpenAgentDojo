"""Shared file-list cache invalidation primitive (Phase 4.A.22).

The sessions router caches ``git ls-files`` results per sandbox so the
quick-open palette doesn't re-shell on every keystroke. Three surfaces
mutate the workspace and therefore MUST drop the cached listing:

  * Workspace ``PUT /sessions/{id}/files`` (write)
  * Workspace ``POST /sessions/{id}/revert`` (per-file undo)
  * Workspace ``POST /sessions/{id}/reset`` (full reset)
  * Agent ``POST /agent/apply-patch`` (the new client of this module)

Before Phase 4.A.22, the cache invalidation lived only inside the
sessions router — meaning a successful ``apply_patch`` from the agent
surface left the cache pinned at the pre-patch file set for up to 30
seconds. The user would type a path into the quick-open palette,
get a stale listing, and the file they just touched would not show
up. Funnelling all four call sites through this module keeps them
from drifting in future refactors.

The cache itself still lives in ``app.sessions.router`` (``_FILES_LIST_CACHE``)
to avoid a circular import — this module imports it lazily inside
:func:`invalidate_for_sandbox` so it can be called from anywhere
without dragging the router module into the import graph at boot.
"""

from __future__ import annotations


def invalidate_for_sandbox(sandbox_id: str) -> None:
    """Drop the cached file listing for ``sandbox_id``.

    Idempotent — calling on an unknown sandbox is a no-op (the cache
    keys by sandbox handle id, which is itself unique per provision).
    Lazy import keeps this module from forcing the sessions router to
    load when callers that don't touch the cache import the symbol.
    """
    try:
        from app.sessions.router import _FILES_LIST_CACHE
    except ImportError:  # pragma: no cover — defensive
        return
    _FILES_LIST_CACHE.pop(sandbox_id, None)


__all__ = ["invalidate_for_sandbox"]
