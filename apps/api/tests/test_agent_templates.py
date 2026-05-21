"""Unit tests for the Jinja-based mission template renderer."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agent.templates import (
    TemplateRenderer,
    clear_template_cache,
    render_response,
)


@pytest.fixture(autouse=True)
def _reset_caches() -> None:
    clear_template_cache()
    yield
    clear_template_cache()


@pytest.fixture()
def mission_folder(tmp_path: Path) -> Path:
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "response.md").write_text(
        (
            "SUMMARY: {{ prompt_summary }}\n"
            "CONTEXT: {{ context_summary }}\n"
            "TITLE: {{ failure_mode_title }}\n"
            "DESC: {{ failure_mode_description }}\n"
            "EXTRA: {{ ticket_id }}\n"
            "UNDEFINED: '{{ never_set }}'\n"
        ),
        encoding="utf-8",
    )
    return tmp_path


def test_renderer_substitutes_all_named_placeholders(mission_folder: Path) -> None:
    renderer = TemplateRenderer(
        mission_id="m1",
        manifest_sha="sha1",
        mission_folder=mission_folder,
    )
    out = renderer.render(
        intent="fix",
        prompt="Reproduce the cookie expiration bug. Then patch it.",
        selected_context=["a.ts", "b.ts"],
        failure_mode_title="Title",
        failure_mode_description="Description",
        extras={"ticket_id": "BUG-42"},
    )
    assert "SUMMARY: Reproduce the cookie expiration bug." in out
    assert "CONTEXT: a.ts, b.ts" in out
    assert "TITLE: Title" in out
    assert "DESC: Description" in out
    assert "EXTRA: BUG-42" in out
    # Missing placeholder must render as the empty string — no exception.
    assert "UNDEFINED: ''" in out


def test_renderer_empty_context_renders_placeholder(mission_folder: Path) -> None:
    out = TemplateRenderer(
        mission_id="m1",
        manifest_sha="sha1",
        mission_folder=mission_folder,
    ).render(intent="fix", prompt="hi", selected_context=[])
    assert "CONTEXT: no files selected" in out


def test_renderer_caches_compiled_template(mission_folder: Path) -> None:
    """Second render of the same (mission, intent) reuses the parsed template."""
    renderer = TemplateRenderer(
        mission_id="m1",
        manifest_sha="sha1",
        mission_folder=mission_folder,
    )
    renderer.render(intent="fix", prompt="hi", selected_context=[])
    # Mutate the template on disk; if the parsed AST is cached we should still
    # see the *original* output.
    (mission_folder / "prompts" / "response.md").write_text(
        "TOTALLY DIFFERENT", encoding="utf-8"
    )
    out2 = renderer.render(intent="fix", prompt="hi", selected_context=[])
    assert "SUMMARY:" in out2  # still using the cached template


def test_renderer_reasoning_returns_empty_when_template_missing(mission_folder: Path) -> None:
    """``render_reasoning`` swallows ``TemplateNotFound`` so missions can omit it."""
    renderer = TemplateRenderer(
        mission_id="m1",
        manifest_sha="sha1",
        mission_folder=mission_folder,
    )
    assert renderer.render_reasoning(
        intent="fix", prompt="hi", selected_context=[]
    ) == ""


def test_render_response_helper_with_real_mission_01(repo_root: Path) -> None:
    """The legacy free-function helper still works against the canonical mission."""
    mission = repo_root / "missions" / "01-auth-cookie-expiration"
    out = render_response(
        manifest_folder=mission,
        prompt="Please fix the expired-cookie bug.",
        selected_context=["backend/src/middleware/requireAuth.ts"],
        failure_mode_title="Agent validates cookie existence but not expiration",
    )
    assert "Thanks" in out
    assert "requireAuth" in out
