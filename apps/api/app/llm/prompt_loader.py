"""Jinja2-backed loader for the prompt templates under ``app/llm/prompts/``.

Each template file under ``prompts/`` has the shape::

    ---
    model: claude-haiku-4-5
    ---
    ---SYSTEM---
    <system prompt text>
    ---USER---
    <user prompt text>

The leading YAML frontmatter records the default logical model id so
the caller can pick the right one without hard-coding it at the call
site. The two markers ``---SYSTEM---`` and ``---USER---`` head the
system / user halves; both halves are Jinja2-rendered with the
``StrictUndefined`` policy so an unset variable surfaces as an
exception, not as the empty string.

Autoescape is OFF because every template renders into LLM prompt
text (not HTML); enabling autoescape would HTML-encode angle brackets
in code snippets, which is exactly what we do not want here. The
opposite risk — prompt injection via untrusted input — is the
caller's responsibility (the substrate trusts its inputs).
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, StrictUndefined

_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_SYSTEM_MARKER = "---SYSTEM---"
_USER_MARKER = "---USER---"


@lru_cache(maxsize=1)
def _env() -> Environment:
    """Return a module-scoped Jinja2 environment.

    The environment is cached so we don't pay the import-time cost on
    every render. Autoescape is OFF (LLM prompt text, not HTML); see
    module docstring.
    """
    return Environment(
        autoescape=False,  # noqa: S701 — LLM prompt text, not HTML; see module docstring
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )


def _read_template(name: str) -> str:
    """Read the raw template file by short name (without extension)."""
    path = _PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"prompt template not found: {path}")
    return path.read_text(encoding="utf-8")


def _strip_frontmatter(raw: str) -> str:
    """Return ``raw`` minus the leading YAML frontmatter block, if any."""
    return _FRONTMATTER_RE.sub("", raw, count=1)


def _split_system_user(body: str) -> tuple[str, str]:
    """Split a template body on the SYSTEM / USER section headers.

    The expected layout is::

        ---SYSTEM---
        <system text>
        ---USER---
        <user text>

    Either header may be omitted to produce an empty half (a prompt
    with no user content, for instance, is legal — the caller appends
    the user message at call time). Missing both markers is a
    template-authoring bug and raises ``ValueError``.
    """
    if _SYSTEM_MARKER not in body and _USER_MARKER not in body:
        raise ValueError(
            f"prompt template missing both {_SYSTEM_MARKER!r} and "
            f"{_USER_MARKER!r} markers; at least one must be present"
        )
    # Carve out the system half: text between ---SYSTEM--- and
    # ---USER--- (or to end of file if no user section). Same shape
    # for the user half but starting at ---USER---.
    system_half = _extract_section(body, _SYSTEM_MARKER, _USER_MARKER)
    user_half = _extract_section(body, _USER_MARKER, None)
    return system_half.strip(), user_half.strip()


def _extract_section(body: str, start_marker: str, end_marker: str | None) -> str:
    """Return text between ``start_marker`` and ``end_marker`` (or EOF)."""
    if start_marker not in body:
        return ""
    _, _, rest = body.partition(start_marker)
    if end_marker is None or end_marker not in rest:
        return rest
    section, _, _ = rest.partition(end_marker)
    return section


def render_prompt(name: str, **vars: object) -> tuple[str, str]:
    """Render the named template and return ``(system_prompt, user_prompt)``.

    Both halves go through Jinja2 with ``StrictUndefined`` — any
    template variable referenced but not passed in raises
    ``jinja2.UndefinedError`` rather than silently rendering as the
    empty string. That is intentional: a missing variable in an LLM
    prompt is a contract bug, not a rendering nuance.
    """
    raw = _read_template(name)
    body = _strip_frontmatter(raw)
    system_template_str, user_template_str = _split_system_user(body)
    env = _env()
    system_prompt = env.from_string(system_template_str).render(**vars)
    user_prompt = env.from_string(user_template_str).render(**vars)
    return system_prompt, user_prompt
