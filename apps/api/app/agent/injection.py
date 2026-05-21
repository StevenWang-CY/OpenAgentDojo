"""Prompt-injection detector (plan §21, M8).

Pure-function pattern matcher that flags common prompt-injection attempts in
user-submitted text. The detector NEVER blocks — it only returns the list of
matched pattern names so the caller can emit a ``validator.flag`` supervision
event for later safety-awareness scoring.

The pattern set is intentionally small and high-signal. False positives on a
benign prompt would pollute the grading signal, so we prefer five tight
patterns over ten leaky ones. Each pattern is regex-based, case-insensitive,
and word-boundary aware where it matters.

The patterns:

* ``ignore_previous`` — classic "ignore the previous instructions" override.
* ``system_prompt_extract`` — attempts to reveal or leak the system prompt.
* ``act_as_dan`` — well-known jailbreak personas (DAN, jailbreak, developer
  mode, evil twin). Conservative on purpose: matches only these named
  personas, never generic ``act as a coding assistant`` phrasing.
* ``instruction_override`` — ``new instructions:`` / ``forget everything
  above`` style overrides.
* ``role_hijack`` — explicit role-play tokens such as ``assistant:``,
  ``system:``, or the ChatML ``<|im_start|>`` sentinel.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Pattern set — keep tight and well-commented. False positives cost grading
# signal; spec says "flag, don't block."
# ---------------------------------------------------------------------------

# "ignore (the) previous/prior/above instruction(s)" — the canonical override.
_IGNORE_PREVIOUS = re.compile(
    r"\bignore\s+(?:the\s+)?(?:previous|prior|above)\s+instructions?\b",
    re.IGNORECASE,
)

# Attempts to reveal / leak the system or hidden prompt.
_SYSTEM_PROMPT_EXTRACT = re.compile(
    r"\b(?:reveal|show|leak|print|dump)\s+(?:the\s+)?(?:system|hidden)\s+prompt\b",
    re.IGNORECASE,
)

# Named jailbreak personas only — DAN, jailbreak, developer mode, evil twin.
# Phrased so generic "act as a coding assistant" never matches.
_ACT_AS_DAN = re.compile(
    r"\b(?:act\s+as|you\s+are|pretend\s+to\s+be)\s+"
    r"(?:dan\b|jailbreak\b|(?:in\s+)?developer\s+mode\b|(?:an?\s+)?evil\s+twin\b)",
    re.IGNORECASE,
)

# "new/updated/latest instructions:" or "forget everything/all/your above/previous/prior".
_INSTRUCTION_OVERRIDE = re.compile(
    r"(?:\b(?:new|updated|latest)\s+instructions?\s*:\s)"
    r"|(?:\bforget\s+(?:everything|all|your)\s+(?:above|previous|prior)\b)",
    re.IGNORECASE,
)

# Role-play hijacks: explicit role tokens and ChatML sentinels.
_ROLE_HIJACK = re.compile(
    r"(?:^|\n)\s*(?:assistant|system)\s*:\s"
    r"|<\|im_start\|>",
    re.IGNORECASE,
)


_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ignore_previous", _IGNORE_PREVIOUS),
    ("system_prompt_extract", _SYSTEM_PROMPT_EXTRACT),
    ("act_as_dan", _ACT_AS_DAN),
    ("instruction_override", _INSTRUCTION_OVERRIDE),
    ("role_hijack", _ROLE_HIJACK),
)


def detect_prompt_injection(text: str) -> list[str]:
    """Return the list of injection-pattern names that match ``text``.

    Empty list means the prompt is clean. The order of returned names matches
    the declaration order above so callers can rely on it for snapshot tests.

    The function is pure: no I/O, no logging, no side effects.
    """
    if not text:
        return []
    return [name for name, pattern in _PATTERNS if pattern.search(text)]
