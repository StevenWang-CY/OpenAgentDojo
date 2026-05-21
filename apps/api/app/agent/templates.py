"""Jinja2-based template renderer for agent responses and reasoning.

The templates live inside each mission's ``prompts/`` sub-folder. Variable
names in the templates are documented in the template files themselves (see
``missions/01-auth-cookie-expiration/prompts/response.md`` for the canonical
example).

Variables always injected:
  prompt_summary             — first sentence (or first 200 chars) of the user prompt
  context_summary            — comma-joined list of selected files, or "no files selected"
  failure_mode_title         — the mission's failure_mode.title (caller fills this)
  failure_mode_description   — the mission's failure_mode.description (caller fills this)

Plus any additional names declared by the manifest under
``agent.template_extras`` (forward-compatible — currently optional, callers may
pass a dict via ``extras``).

A missing placeholder renders as an empty string instead of raising — this
keeps mission authors from breaking the agent path while iterating on
templates. ``TemplateRenderer`` caches the compiled Jinja template per
``(mission_id, intent)`` so repeated turns within a session reuse the parsed
AST.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from jinja2 import (
    ChainableUndefined,
    Environment,
    FileSystemLoader,
    Template,
    TemplateNotFound,
)


class _SilentUndefined(ChainableUndefined):
    """Render any undefined variable as the empty string."""

    __slots__ = ()

    def __str__(self) -> str:
        return ""

    def __html__(self) -> str:  # pragma: no cover — autoescape disabled
        return ""

    def __bool__(self) -> bool:
        return False

    def __iter__(self):
        return iter(())

    def __getattr__(self, _name: str) -> _SilentUndefined:
        return self


_ENV_CACHE: dict[str, Environment] = {}
_TEMPLATE_CACHE: dict[tuple[str, str, str], Template] = {}
_CACHE_LOCK = threading.Lock()


def _env_for(prompts_dir: Path) -> Environment:
    """Return a (cached) Jinja env scoped to one mission's prompts folder."""
    key = str(prompts_dir.resolve())
    with _CACHE_LOCK:
        env = _ENV_CACHE.get(key)
        if env is None:
            env = Environment(
                loader=FileSystemLoader(key),
                autoescape=False,  # noqa: S701 — templates render plaintext for the agent narrator, not HTML
                keep_trailing_newline=True,
                trim_blocks=True,
                lstrip_blocks=True,
                undefined=_SilentUndefined,
            )
            _ENV_CACHE[key] = env
        return env


def clear_template_cache() -> None:
    """Drop all cached templates/envs. Intended for tests."""
    with _CACHE_LOCK:
        _ENV_CACHE.clear()
        _TEMPLATE_CACHE.clear()


class TemplateRenderer:
    """Render mission prompt templates for a given intent.

    Constructed per ``(mission_id, manifest_sha256)`` by ``AgentService`` and
    cached so we don't re-parse the same template on every turn.
    """

    __slots__ = ("_env", "manifest_sha", "mission_id", "prompts_dir")

    def __init__(self, mission_id: str, manifest_sha: str, mission_folder: Path):
        self.mission_id = str(mission_id)
        self.manifest_sha = str(manifest_sha)
        self.prompts_dir = (Path(mission_folder) / "prompts").resolve()
        self._env = _env_for(self.prompts_dir)

    # ------------------------------------------------------------------
    # public render API
    # ------------------------------------------------------------------

    def render(
        self,
        intent: str,
        *,
        prompt: str,
        selected_context: list[str],
        failure_mode_title: str = "",
        failure_mode_description: str = "",
        extras: dict[str, Any] | None = None,
        template_name: str | None = None,
    ) -> str:
        """Render the template for ``intent`` and return its text.

        ``intent`` is informational — it is included in the cache key and
        defaults the template name to ``response.md``. Missions can override
        with ``template_name`` (e.g. ``"reasoning.md"`` for the reasoning
        trace).
        """
        name = template_name or "response.md"
        template = self._template(intent, name)
        context = {
            "prompt_summary": _extract_first_sentence(prompt),
            "context_summary": _format_context(selected_context),
            "failure_mode_title": failure_mode_title,
            "failure_mode_description": failure_mode_description,
        }
        if extras:
            for k, v in extras.items():
                context.setdefault(k, v)
        return template.render(**context)

    def render_reasoning(
        self,
        intent: str,
        *,
        prompt: str,
        selected_context: list[str],
        failure_mode_title: str = "",
        failure_mode_description: str = "",
        extras: dict[str, Any] | None = None,
    ) -> str:
        """Render ``reasoning.md`` if present, otherwise return ``""``."""
        try:
            return self.render(
                intent,
                prompt=prompt,
                selected_context=selected_context,
                failure_mode_title=failure_mode_title,
                failure_mode_description=failure_mode_description,
                extras=extras,
                template_name="reasoning.md",
            )
        except TemplateNotFound:
            return ""

    # ------------------------------------------------------------------
    # cache helpers
    # ------------------------------------------------------------------

    def _template(self, intent: str, template_name: str) -> Template:
        cache_key = (self.mission_id, self.manifest_sha, f"{intent}:{template_name}")
        with _CACHE_LOCK:
            cached = _TEMPLATE_CACHE.get(cache_key)
        if cached is not None:
            return cached
        template = self._env.get_template(template_name)
        with _CACHE_LOCK:
            _TEMPLATE_CACHE[cache_key] = template
        return template


# ---------------------------------------------------------------------------
# Free-function helpers (kept for back-compat with router.py and tests).
# ---------------------------------------------------------------------------


def render_response(
    manifest_folder: Path,
    prompt: str,
    selected_context: list[str],
    failure_mode_title: str = "",
    failure_mode_description: str = "",
) -> str:
    """Render ``prompts/response.md`` for a mission and return the text."""
    renderer = TemplateRenderer(
        mission_id=manifest_folder.name,
        manifest_sha="",
        mission_folder=manifest_folder,
    )
    return renderer.render(
        intent="response",
        prompt=prompt,
        selected_context=selected_context,
        failure_mode_title=failure_mode_title,
        failure_mode_description=failure_mode_description,
    )


def render_reasoning(
    manifest_folder: Path,
    prompt: str,
    selected_context: list[str],
    failure_mode_title: str = "",
    failure_mode_description: str = "",
) -> str:
    """Render ``prompts/reasoning.md`` for a mission. Returns "" if missing."""
    renderer = TemplateRenderer(
        mission_id=manifest_folder.name,
        manifest_sha="",
        mission_folder=manifest_folder,
    )
    return renderer.render_reasoning(
        intent="reasoning",
        prompt=prompt,
        selected_context=selected_context,
        failure_mode_title=failure_mode_title,
        failure_mode_description=failure_mode_description,
    )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _extract_first_sentence(text: str) -> str:
    """Return the first sentence or the first 200 characters, whichever is shorter."""
    text = (text or "").strip()
    if not text:
        return ""
    # Try to split on the first sentence-ending punctuation followed by whitespace.
    for i, ch in enumerate(text):
        if ch in ".!?" and (i + 1 >= len(text) or text[i + 1].isspace()):
            sentence = text[: i + 1].strip()
            if len(sentence) <= 200:
                return sentence
            break
    # Fall back to first 200 chars, trimmed to last word boundary.
    snippet = text[:200]
    if len(text) > 200:
        # Cut at last space to avoid mid-word truncation.
        last_space = snippet.rfind(" ")
        if last_space > 0:
            snippet = snippet[:last_space]
        snippet = snippet.rstrip(" .,;:!?") + "…"
    return snippet


def _format_context(selected_context: list[str]) -> str:
    if not selected_context:
        return "no files selected"
    return ", ".join(selected_context)


__all__ = [
    "TemplateRenderer",
    "clear_template_cache",
    "render_reasoning",
    "render_response",
]
