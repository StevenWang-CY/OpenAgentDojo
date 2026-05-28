"""Canonical input hasher for the LLM cache (P1 §0.4.3).

The cache key uses SHA-256 over a JSON-canonicalised form of the
caller's input payload. The transform here is pure:

  * keys are sorted lexicographically;
  * no whitespace between tokens;
  * ``ensure_ascii=False`` so non-ASCII content hashes the same on
    every platform without lossy escapes.

Rounding rules (e.g. ``round(x, 1)`` on floating-point dimensions to
absorb trivial floating-point drift) are the **caller's**
responsibility. The hasher is a deterministic transform — applying
rounding here would couple every domain to one global rule, which is
neither correct (different domains carry different precision needs)
nor desirable (the per-domain rules are documented in
:mod:`app.llm.domains`).

Tuples and lists serialise the same way under :func:`json.dumps`
(both → JSON array). Tuples are preferred in input models to
communicate ordering intent at the type level, but the hash they
produce is identical to the equivalent list. Callers MUST sort or
preserve order intentionally — :func:`canonical_content_hash` does
not re-order array entries.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_content_hash(payload: dict[str, Any]) -> str:
    """SHA-256 hex of JSON-canonicalised ``payload``.

    Canonical form: sorted keys, no whitespace, ``ensure_ascii=False``,
    ``allow_nan=False``. Rounding of floats / coercion of mixed types
    is the caller's responsibility (see per-domain rules in
    :mod:`app.llm.domains`).

    ``allow_nan=False`` rejects non-finite floats. The cache-key
    computation is one of the rare callers where it is preferable to
    raise loud (``ValueError`` from :func:`json.dumps`) rather than
    silently coerce to ``null`` — a NaN slipping into a cache key
    silently bakes the bad value into every downstream lookup. Callers
    seeing this raise should clamp upstream and document the fix.
    """
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
