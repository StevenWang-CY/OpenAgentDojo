"""Deterministic intent classifier for agent prompts.

Determinism is load-bearing here: the same (prompt, intents_file_hash) pair
MUST classify to the same intent on every run. There is no LLM on this path.

Algorithm (plan §8.4):
  1. Load the mission-scoped intent keyword sets from
     ``mission.agent.intents_file`` (a YAML file with the shape shown in
     ``missions/01-auth-cookie-expiration/prompts/intents.yaml``).
  2. Case-fold the incoming prompt.
  3. Walk the canonical priority order (``fix > test > revise > narrow``)
     and return the first intent whose keyword set contains any substring
     that appears in the prompt.
  4. Fall back to ``unknown``.

Keyword *priority* matters: the same prompt could match both ``fix`` and
``narrow``; ``fix`` wins so the agent applies the patch.

If a mission lacks ``prompts/intents.yaml`` the default keyword set (mirroring
Mission 01) is used so missions can be authored incrementally without breaking
classification.
"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

IntentKind = Literal["fix", "test", "revise", "narrow", "unknown"]

# Priority order is canonical. Do not reorder without bumping the docs.
INTENT_PRIORITY: tuple[IntentKind, ...] = ("fix", "test", "revise", "narrow", "unknown")


@dataclass(slots=True, frozen=True)
class IntentMap:
    """Parsed intents file — keyword lists keyed by intent name.

    ``source_sha256`` lets callers tag events / cache keys with the content
    hash so determinism across replays is verifiable.
    """

    keywords: dict[IntentKind, tuple[str, ...]]
    source_sha256: str = ""
    source_path: str | None = field(default=None)

    def keywords_for(self, intent: IntentKind) -> tuple[str, ...]:
        return self.keywords.get(intent, ())


# A safe default map used when a mission omits its intents file. The keywords
# mirror the canonical Mission 01 set so the classifier still works in tests
# and in missions that haven't authored a bespoke intents.yaml yet.
_DEFAULT_KEYWORDS: dict[IntentKind, tuple[str, ...]] = {
    "fix": ("fix", "repair", "debug", "investigate", "address"),
    "test": ("test", "regression", "cover", "assert"),
    "revise": ("revise", "retry", "again", "redo"),
    "narrow": ("narrow", "scope", "only"),
    "unknown": (),
}

DEFAULT_INTENT_MAP = IntentMap(keywords=_DEFAULT_KEYWORDS, source_sha256="", source_path=None)


def _coerce_keywords(raw: Any) -> tuple[str, ...]:
    """Accept either a list of strings or the ``{keywords: [...]}`` shape."""
    if raw is None:
        return ()
    if isinstance(raw, dict):
        kws = raw.get("keywords", [])
    elif isinstance(raw, list):
        kws = raw
    else:
        return ()
    out: list[str] = []
    for k in kws:
        if isinstance(k, str) and k.strip():
            out.append(k.strip().lower())
    return tuple(out)


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------
#
# The intent map for a mission is immutable for the lifetime of the manifest
# (any content edit bumps ``manifest_sha256``). We cache the parsed map keyed
# by ``(mission_id, file_sha256)`` so repeated prompt submissions skip the YAML
# parse round-trip without losing reload-on-change behaviour.

_INTENT_CACHE: dict[tuple[str, str], IntentMap] = {}
_INTENT_CACHE_LOCK = threading.Lock()


def clear_intent_cache() -> None:
    """Drop all cached intent maps. Intended for tests."""
    with _INTENT_CACHE_LOCK:
        _INTENT_CACHE.clear()


def load_intents(mission: Any) -> IntentMap:
    """Load the per-mission intent keyword map.

    ``mission`` is either a :class:`MissionManifest` instance or a wrapper
    exposing ``folder`` + ``manifest`` (a ``LoadedMission`` or the private
    proxy in ``app.agent.service``). Missing ``agent.intents_file`` → default
    map. Missing file on disk → default map. Malformed YAML → default map.
    """
    folder, manifest = _split_loaded(mission)
    agent_cfg = getattr(manifest, "agent", None)
    intents_file_rel = getattr(agent_cfg, "intents_file", None) if agent_cfg else None
    if not intents_file_rel or folder is None:
        return DEFAULT_INTENT_MAP

    intents_path = (folder / intents_file_rel).resolve()
    if not intents_path.exists():
        return DEFAULT_INTENT_MAP

    raw_bytes = intents_path.read_bytes()
    sha = hashlib.sha256(raw_bytes).hexdigest()

    mission_id = str(getattr(manifest, "id", "") or "")
    cache_key = (mission_id, sha)
    with _INTENT_CACHE_LOCK:
        cached = _INTENT_CACHE.get(cache_key)
    if cached is not None:
        return cached

    try:
        data = yaml.safe_load(raw_bytes.decode("utf-8")) or {}
    except yaml.YAMLError:
        return DEFAULT_INTENT_MAP

    intents_block = data.get("intents") if isinstance(data, dict) else None
    if not isinstance(intents_block, dict):
        return DEFAULT_INTENT_MAP

    keywords: dict[IntentKind, tuple[str, ...]] = {}
    for intent in INTENT_PRIORITY:
        keywords[intent] = _coerce_keywords(intents_block.get(intent))
    # Always include an unknown slot for completeness.
    keywords.setdefault("unknown", ())

    intent_map = IntentMap(
        keywords=keywords,
        source_sha256=sha,
        source_path=str(intents_path),
    )
    with _INTENT_CACHE_LOCK:
        _INTENT_CACHE[cache_key] = intent_map
    return intent_map


def _split_loaded(mission: Any) -> tuple[Path | None, Any]:
    """Resolve ``(folder, manifest)`` from a manifest or LoadedMission."""
    folder = getattr(mission, "folder", None)
    if folder is None:
        # ``mission`` is likely the manifest itself; the caller had no folder
        # context — DEFAULT_INTENT_MAP will be returned upstream.
        return None, mission
    return Path(folder), getattr(mission, "manifest", mission)


class IntentClassifier:
    """Stateless callable that maps a prompt to one of the canonical intents.

    Constructed per ``(mission_id, manifest_sha256)`` by ``AgentService`` and
    cached so repeated submissions don't re-parse the YAML.
    """

    __slots__ = ("intent_map",)

    def __init__(self, intent_map: IntentMap):
        self.intent_map = intent_map

    @classmethod
    def for_mission(cls, mission: Any) -> IntentClassifier:
        return cls(load_intents(mission))

    @classmethod
    def default(cls) -> IntentClassifier:
        return cls(DEFAULT_INTENT_MAP)

    def __call__(self, prompt: str) -> IntentKind:
        return classify(prompt, self.intent_map)


def classify(prompt: str, intents: IntentMap | None = None) -> IntentKind:
    """Classify a prompt to one of the five canonical intents.

    Order matters: ``fix`` is checked before ``narrow`` so a prompt like
    "please fix the bug with a minimal diff" classifies as ``fix``, not
    ``narrow``.
    """
    if intents is None:
        intents = DEFAULT_INTENT_MAP
    text = (prompt or "").strip().lower()
    if not text:
        return "unknown"
    for intent in INTENT_PRIORITY:
        if intent == "unknown":
            continue
        for keyword in intents.keywords_for(intent):
            if keyword and keyword in text:
                return intent
    return "unknown"


__all__ = [
    "DEFAULT_INTENT_MAP",
    "INTENT_PRIORITY",
    "IntentClassifier",
    "IntentKind",
    "IntentMap",
    "classify",
    "clear_intent_cache",
    "load_intents",
]
