"""Contributor-side LLM CLI for the P1-1 mission-authoring scaffold.

This module is intentionally a *contributor accelerator* — it is invoked
at authoring time from ``scripts/mission-template/init.py --with-llm-draft``
to seed a new mission directory with model-generated draft artefacts. It
must NOT be wired into the request path: there is no caching, no per-user
indexing, and no DB session — see ``app.llm.domains`` for the runtime
chokepoints, all of which route through :func:`app.llm.cache.get_or_generate`.

Contract notes
--------------

* The draft writes into a ``_draft/`` subdirectory inside the mission
  folder. The mission loader (``app/missions/loader.py``) refuses to
  load any mission folder that still contains ``_draft/``, so this
  output cannot accidentally ship — the author MUST hand-promote each
  artefact into its canonical location and then delete ``_draft/``.

* The model id is fixed at ``claude-opus-4-7`` (sourced from the prompt
  template's frontmatter — kept in sync here so a contributor running
  the CLI sees the same id in their telemetry as the prompt declares).

* Telemetry is best-effort: we bump
  ``llm_generation_succeeded_total{domain="mission_authoring_draft",
  model_id="claude-opus-4-7"}`` when the registry is reachable, but
  the CLI does NOT depend on observability bootstrapping (a contributor
  on a laptop without prometheus_client installed should still get a
  draft).

* Credentials are sourced from the environment via the standard
  ``app.llm.client.build_anthropic_client`` factory (which delegates to
  ``app.agent.llm.AnthropicClient``). No env-var value is ever printed
  or logged — we only emit the masked outcome ("LLM call succeeded")
  and the path the artefacts were written to.

Usage
-----

::

    python -m app.llm.cli mission-authoring-draft \\
        --mission-dir <repo>/missions/14-new-mission-id \\
        --repo-pack-id fullstack-auth-demo \\
        --failure-mode-title "Race condition between login and profile fetch" \\
        --seed-outline-file <seed.txt>

The CLI exits non-zero if the mission directory does not exist, if it
already contains a ``_draft/`` subdirectory, or if the LLM call raises.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

# Per the prompt frontmatter (``app/llm/prompts/mission_authoring_draft.md``).
_DRAFT_MODEL: str = "claude-opus-4-7"
_DRAFT_DOMAIN: str = "mission_authoring_draft"
# Anthropic API hard ceiling for opus is generous; we bound at a value
# wide enough for the literal output shape (TITLE / FAILURE MODE / FILES
# / ACCEPTANCE HINT) plus a draft README narrative.
_DRAFT_MAX_TOKENS: int = 2048

# Repo-pack → file extension for the generated hidden-test stub. The
# mapping mirrors ``scripts/mission-template/init.py::_REPO_PACKS``; we
# do not import that module to keep the CLI free of cross-tree imports.
_REPO_PACK_LANGUAGE_EXT: dict[str, str] = {
    "fullstack-auth-demo": "ts",
    "data-api-demo": "py",
    "go-orders-service": "go",
}


# ---------------------------------------------------------------------------
# Public API.
# ---------------------------------------------------------------------------


async def generate_mission_authoring_draft(
    *,
    mission_dir: Path,
    repo_pack_id: str,
    failure_mode_title: str,
    seed_outline: str,
    client: Any | None = None,
) -> Path:
    """Generate the LLM draft and write artefacts into ``mission_dir/_draft/``.

    Returns the path of the ``_draft/`` directory. Raises ``FileNotFoundError``
    if ``mission_dir`` does not exist and ``FileExistsError`` if a previous
    draft is already present (so a contributor running the CLI twice never
    silently clobbers the prior output).
    """
    if not mission_dir.is_dir():  # noqa: ASYNC240 — one-shot CLI helper; a sync existence check is not a request hot path
        raise FileNotFoundError(
            f"mission directory not found: {mission_dir} — scaffold the mission "
            "skeleton first with ``scripts/mission-template/init.py``."
        )

    draft_dir = mission_dir / "_draft"
    if draft_dir.exists():
        raise FileExistsError(
            f"draft directory {draft_dir} already exists; delete it (after "
            "promoting any wanted artefacts) before regenerating."
        )

    # Render the prompt eagerly so a template error surfaces before we
    # ever instantiate the client.
    from app.llm.prompt_loader import render_prompt

    system, user_prompt = render_prompt(
        _DRAFT_DOMAIN,
        repo_pack_id=repo_pack_id,
        failure_mode_title=failure_mode_title,
        seed_outline=seed_outline,
    )

    if client is None:
        from app.llm.client import build_anthropic_client

        client = build_anthropic_client(_DRAFT_MODEL)

    resp = await client.messages_create(
        model=_DRAFT_MODEL,
        max_tokens=_DRAFT_MAX_TOKENS,
        system=[
            {
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_prompt}],
    )
    draft_text = _extract_text(resp)

    # Materialise the draft tree. We write the model's literal output to
    # ``ideal_solution.draft.md`` (the model returns the TITLE / FAILURE
    # MODE / FILES / ACCEPTANCE HINT block) and stamp empty skeletons for
    # the other artefacts the mission contract expects so the author has
    # a clear hand-promotion checklist.
    draft_dir.mkdir(parents=True)
    language_ext = _REPO_PACK_LANGUAGE_EXT.get(repo_pack_id, "txt")

    (draft_dir / "ideal_solution.draft.md").write_text(draft_text, encoding="utf-8")
    (draft_dir / "ideal_solution.draft.diff").write_text(
        _PLACEHOLDER_DIFF.format(kind="ideal solution"),
        encoding="utf-8",
    )
    (draft_dir / "agent_patch.draft.diff").write_text(
        _PLACEHOLDER_DIFF.format(kind="agent patch (the bug the supervisor catches)"),
        encoding="utf-8",
    )
    prompts_dir = draft_dir / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "response.draft.md").write_text(
        _PLACEHOLDER_RESPONSE,
        encoding="utf-8",
    )
    hidden_dir = draft_dir / "hidden_tests"
    hidden_dir.mkdir()
    (hidden_dir / f"auth.hidden.draft.{language_ext}").write_text(
        _PLACEHOLDER_HIDDEN_TEST.format(language=language_ext),
        encoding="utf-8",
    )

    _bump_success_counter()
    return draft_dir


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


_PLACEHOLDER_DIFF = """\
# DRAFT — REPLACE BEFORE SHIPPING
#
# This file was stamped by ``app.llm.cli`` as part of the mission-
# authoring scaffold (P1-1). The {kind} must be written by hand —
# the LLM draft is intentionally NOT a runnable patch. See the
# rendered ideal_solution.draft.md for the model's outline.
"""

_PLACEHOLDER_RESPONSE = """\
# DRAFT — REPLACE BEFORE SHIPPING

The agent response template MUST be authored by hand. The LLM is not
trusted to write the canonical agent prose because it would otherwise
encode its own fix shape into the simulator's "agent" surface — that
would conflate the agent persona with the supervisor's expected
intervention. Use the model's ideal_solution.draft.md as a brief; do
not paste its prose into a published mission.
"""

_PLACEHOLDER_HIDDEN_TEST = """\
// DRAFT — REPLACE BEFORE SHIPPING
//
// This is a hidden-test stub generated by the mission-authoring
// scaffold. Hidden tests must be hand-authored against the runtime
// you actually intend to assert ({language}). The supervisor's grade
// turns on these tests passing AFTER the fix lands; they MUST NOT
// pass against the agent_patch.diff.
"""


def _extract_text(resp: Any) -> str:
    """Pull the first text block from an Anthropic SDK response.

    Mirrors :func:`app.reports.coaching._extract_text` — different mock
    shapes (and the live SDK) surface the text in slightly different
    places, so we tolerate all of them rather than couple to one.
    """
    content = getattr(resp, "content", None)
    if isinstance(content, list) and content:
        head = content[0]
        text = getattr(head, "text", None)
        if isinstance(text, str):
            return text
        if isinstance(head, dict):
            dict_text = head.get("text")
            if isinstance(dict_text, str):
                return dict_text
    if isinstance(content, str):
        return content
    raise RuntimeError("mission_authoring_draft: unexpected LLM response shape")


def _bump_success_counter() -> None:
    """Best-effort bump of the success counter.

    Importing ``app.observability`` pulls in ``prometheus_client``. A
    contributor running the CLI from a checkout that hasn't installed
    the API venv would otherwise crash here. We swallow ImportError
    silently — telemetry is observability, not correctness.
    """
    try:
        from app.observability import llm_generation_succeeded_total

        llm_generation_succeeded_total.labels(domain=_DRAFT_DOMAIN, model_id=_DRAFT_MODEL).inc()
    except Exception:
        return


def _read_seed_outline(args: argparse.Namespace) -> str:
    """Resolve the seed outline from either ``--seed-outline`` or a file."""
    if args.seed_outline is not None:
        # argparse Namespace attrs are typed ``Any``; the --seed-outline value
        # is always a str at runtime, so coerce to satisfy the ``-> str`` contract.
        return str(args.seed_outline)
    if args.seed_outline_file is not None:
        path = Path(args.seed_outline_file)
        if not path.exists():
            raise SystemExit(f"seed outline file not found: {path}")
        return path.read_text(encoding="utf-8")
    raise SystemExit("one of --seed-outline or --seed-outline-file is required")


# ---------------------------------------------------------------------------
# CLI entry point.
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="app.llm.cli",
        description=(
            "Contributor-side LLM CLI for the mission-authoring scaffold "
            "(P1-1). See ``app/llm/cli.py`` for the contract."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    draft = sub.add_parser(
        "mission-authoring-draft",
        help="Generate a mission-draft proposal into <mission-dir>/_draft/.",
    )
    draft.add_argument(
        "--mission-dir",
        type=Path,
        required=True,
        help="Path to the mission directory that should receive ``_draft/``.",
    )
    draft.add_argument(
        "--repo-pack-id",
        required=True,
        help=("Repo pack id the mission targets (one of the keys in missions/_shared/repos/)."),
    )
    draft.add_argument(
        "--failure-mode-title",
        required=True,
        help="Human-readable failure-mode title (matches the prompt template).",
    )
    draft.add_argument(
        "--seed-outline",
        default=None,
        help="Inline seed outline; mutually exclusive with --seed-outline-file.",
    )
    draft.add_argument(
        "--seed-outline-file",
        type=Path,
        default=None,
        help="Path to a file containing the seed outline (UTF-8).",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "mission-authoring-draft":
        seed_outline = _read_seed_outline(args)
        try:
            draft_dir = asyncio.run(
                generate_mission_authoring_draft(
                    mission_dir=args.mission_dir,
                    repo_pack_id=args.repo_pack_id,
                    failure_mode_title=args.failure_mode_title,
                    seed_outline=seed_outline,
                )
            )
        except FileExistsError as exc:
            print(f"[llm-cli] {exc}", file=sys.stderr)
            return 2
        except FileNotFoundError as exc:
            print(f"[llm-cli] {exc}", file=sys.stderr)
            return 2
        except RuntimeError as exc:
            # ``AnthropicClient`` raises RuntimeError("LLM provider not
            # configured") when civitas_core is unavailable. Surface the
            # operator-actionable instruction without leaking env-var
            # values.
            print(
                f"[llm-cli] LLM call failed: {exc}\n"
                "  Set ANTHROPIC_API_KEY (or ANTHROPIC_PROVIDER=bedrock + "
                "AWS_BEARER_TOKEN_BEDROCK + AWS_REGION) and retry.",
                file=sys.stderr,
            )
            return 3
        print(
            "[llm-cli] LLM draft written to "
            f"{draft_dir} — review each file and hand-promote into the "
            "canonical mission layout before deleting ``_draft/``."
        )
        return 0

    parser.print_help(sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
