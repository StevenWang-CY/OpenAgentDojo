"""Supervision event schema.

The legacy ``SupervisionEventOut`` model was removed: no router referenced
it as a ``response_model`` (the timeline route returns
``app.schemas.workspace.SupervisionEventRead`` and the WS path serialises
``SupervisionEvent`` rows directly as ``dict``). Importers should switch to
``SupervisionEventRead`` from ``app.schemas.workspace``.
"""

from __future__ import annotations
