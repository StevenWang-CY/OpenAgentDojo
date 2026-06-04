"""Pytest bootstrap for the data-api-demo backend.

The grading sandbox copies this pack (``.venv`` included) into a scratch
dir and applies the candidate diff there. The ``.venv`` ships an *editable*
install of ``app`` whose path can resolve back outside the sandbox, so a
top-level ``from app.X import ...`` in a test could bind to the ORIGINAL
pack instead of the applied copy — silently grading the wrong code.

Force the package that lives next to THIS conftest (i.e. the copy actually
under test) to win: put its directory first on ``sys.path`` and drop any
``app`` modules a stray editable finder pre-imported.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if sys.path and sys.path[0] != _HERE:
    sys.path.insert(0, _HERE)

for _name in [m for m in sys.modules if m == "app" or m.startswith("app.")]:
    del sys.modules[_name]
