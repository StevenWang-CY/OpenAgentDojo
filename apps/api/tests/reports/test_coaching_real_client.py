"""Smoke check for :func:`app.reports.coaching._build_client`.

The factory is otherwise only exercised through monkeypatched stubs;
without this test a refactor that drops the ``AnthropicClient`` import
or renames the kwargs would slip through every other coaching test.

We import + call ``_build_client()`` and assert the returned object
exposes ``messages_create`` and is wired to the documented coaching
model. We DO NOT make a network call — the AnthropicClient constructor
is pure; no HTTP happens until ``.messages_create`` is awaited.
"""

from __future__ import annotations


def test_build_client_returns_messages_create_capable_object() -> None:
    """_build_client returns a live AnthropicClient configured for coaching."""
    from app.reports import coaching as coaching_module
    from app.reports.coaching import _COACHING_MODEL

    client = coaching_module._build_client()

    # The chokepoint calls ``client.messages_create(...)`` — the
    # contract is just that the attribute exists and is callable.
    assert hasattr(client, "messages_create")
    assert callable(client.messages_create)

    # Model is the documented coaching model. AnthropicClient stores
    # it on ``model_logical`` (the SDK resolution happens lazily at
    # call time). We read it off the instance to defend against a
    # rename that silently routes coaching through a cheaper /
    # weaker model.
    model = getattr(client, "model_logical", None)
    assert model == _COACHING_MODEL == "claude-sonnet-4-6", (
        f"coaching must be wired to {_COACHING_MODEL!r}, got {model!r}"
    )
