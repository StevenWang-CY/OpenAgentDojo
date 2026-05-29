"""OpenAPI content-type contract for the replay endpoints (P1-6).

The two replay endpoints (``GET .../replay.json`` and
``GET .../replay.zip``) return a :class:`Response` whose ``media_type``
FastAPI cannot infer from the handler signature. Without explicit
``responses=`` content blocks the generated OpenAPI advertises no
content schema for the 200, so the lead's contract regen would publish
a dishonest ``200`` with no media type — third-party verifiers and the
typed FE client lose the ``application/json`` / ``application/zip``
guarantee. These tests pin the content-types directly off ``app.openapi()``
so a regression in the ``responses=`` declarations flips them.
"""

from __future__ import annotations

from typing import Any


def _openapi() -> dict[str, Any]:
    from app.main import create_app

    app = create_app()
    return app.openapi()


def test_replay_json_advertises_json_object_schema() -> None:
    spec = _openapi()
    path = spec["paths"]["/api/v1/submissions/{submission_id}/replay.json"]
    ok = path["get"]["responses"]["200"]
    content = ok["content"]
    assert "application/json" in content, content
    schema = content["application/json"]["schema"]
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is True


def test_replay_zip_advertises_zip_binary_schema() -> None:
    spec = _openapi()
    path = spec["paths"]["/api/v1/submissions/{submission_id}/replay.zip"]
    ok = path["get"]["responses"]["200"]
    content = ok["content"]
    assert "application/zip" in content, content
    schema = content["application/zip"]["schema"]
    assert schema["type"] == "string"
    assert schema["format"] == "binary"


def test_replay_error_responses_unchanged() -> None:
    """The 404 / 503 declarations stay description-only — the content-type
    fix only touches the 200 entries."""
    spec = _openapi()
    for suffix in ("replay.json", "replay.zip"):
        responses = spec["paths"][
            f"/api/v1/submissions/{{submission_id}}/{suffix}"
        ]["get"]["responses"]
        assert "404" in responses
        assert "503" in responses
        # Error entries carry only a description, no content block.
        assert "content" not in responses["404"]
        assert "content" not in responses["503"]
