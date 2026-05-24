"""Prometheus metrics + structured logging + (optional) OpenTelemetry.

We define the metric objects up-front so handlers can import them without
worrying about registration order. The ASGI ``/metrics`` app is mounted in
:mod:`app.main`.

Logging:

* In ``ARENA_ENV=development`` we keep a colourised human-readable format.
* In every other environment we emit one-JSON-object-per-line, with a redact
  filter that masks sensitive fields (``email``, ``token``, ``cookie``,
  ``api_key``, ``password``, ``secret``, ``prompt``, ``user_prompt``,
  ``agent_response``).

OpenTelemetry:

* If ``OTEL_EXPORTER_OTLP_ENDPOINT`` is set, we install the FastAPI and
  SQLAlchemy auto-instrumentations. The OTel SDK is an optional dependency
  — if it is not importable we log a warning and continue.
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any

from loguru import logger
from prometheus_client import (
    CONTENT_TYPE_LATEST,  # noqa: F401  — re-exported for convenience
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    make_asgi_app,
)

# A dedicated registry keeps tests isolated (no cross-process duplication).
REGISTRY = CollectorRegistry(auto_describe=True)

sessions_active = Gauge(
    "sessions_active",
    "Number of currently-active arena sessions.",
    registry=REGISTRY,
)
sessions_provision_seconds = Histogram(
    "sessions_provision_seconds",
    "Time to provision a sandbox for a session.",
    buckets=(0.5, 1, 2, 5, 10, 20, 30, 60, 120),
    registry=REGISTRY,
)
submissions_total = Counter(
    "submissions_total",
    "Number of submissions graded.",
    # outcome ∈ {graded, failed, timeout}. ``timeout`` is split out from
    # ``failed`` so SLO dashboards can distinguish "we hit the wall-clock
    # budget" (operational) from "the pipeline raised mid-flight" (bug).
    ["mission_id", "outcome"],
    registry=REGISTRY,
)
submissions_score_histogram = Histogram(
    "submissions_score",
    "Final submission scores (0-100).",
    ["mission_id"],
    buckets=(0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100),
    registry=REGISTRY,
)
agent_responses_total = Counter(
    "agent_responses_total",
    "Agent responses generated.",
    ["mission_id", "source"],  # source = template | llm
    registry=REGISTRY,
)
agent_llm_fallback_total = Counter(
    "agent_llm_fallback_total",
    "Times the LLM narration fell back to the deterministic template.",
    ["reason"],
    registry=REGISTRY,
)
llm_calls_total = Counter(
    "llm_calls_total",
    "Total LLM API calls.",
    ["provider", "model", "outcome"],
    registry=REGISTRY,
)
llm_latency_seconds = Histogram(
    "llm_latency_seconds",
    "LLM call latency.",
    ["provider", "model"],
    buckets=(0.1, 0.3, 0.5, 1, 2, 5, 10, 30),
    registry=REGISTRY,
)
event_publish_failures_total = Counter(
    "event_publish_failures_total",
    "Supervision event Redis publishes that did not reach Redis.",
    ["reason"],  # reason = no_redis | publish_error | serialisation_error
    registry=REGISTRY,
)
event_payload_truncated_total = Counter(
    "event_payload_truncated_total",
    "Supervision events whose payload exceeded the wire-size budget "
    "and were truncated at emit time.",
    ["event_type"],
    registry=REGISTRY,
)
profile_malformed_reports_total = Counter(
    "profile_malformed_reports_total",
    "Submissions whose score_report was skipped by the profile radar aggregator.",
    # reason = not_dict | dimensions_missing | dimension_payload_not_dict |
    #          score_not_numeric | unknown_dimension
    ["reason"],
    registry=REGISTRY,
)
# P0-4 — give-up affordance instrumentation. The grading runner increments
# this counter once per submission that landed via the give-up path. The
# ``cap_applied`` label distinguishes "cap was binding" (uncapped > 50) from
# "give-up recorded but cap not binding" (uncapped <= 50) — operators care
# about both signals for content tuning.
give_ups_total = Counter(
    "give_ups_total",
    "Sessions submitted via the give-up affordance (P0-4).",
    ["mission_id", "cap_applied"],  # cap_applied ∈ {"true", "false"}
    registry=REGISTRY,
)
# P0-4 — incremented at the endpoint when the 10-min gate rejects a request.
# Lets ops spot users hitting the gate at scale (signals frustration or
# gate misconfiguration).
give_up_blocked_total = Counter(
    "give_up_blocked_total",
    "Give-up requests rejected by the 10-minute soft block.",
    ["mission_id"],
    registry=REGISTRY,
)
# P0-3 — incremented by the report-page Retry CTA. Distinct from
# submissions_total so we can compute retry-rate per mission without
# disaggregating the broader submissions counter.
mission_retries_total = Counter(
    "mission_retries_total",
    "Sessions created via the Retry-mission CTA (P0-3).",
    ["mission_id"],
    registry=REGISTRY,
)
# P0-5 — incremented by POST /me/consent for every persisted consent row.
# Labels distinguish kind (analytics|functional|marketing) and granted
# (true|false) so the consent funnel can be split out per-kind in dashboards
# (e.g. "what fraction of users grant analytics on first prompt?").
consent_recorded_total = Counter(
    "consent_recorded_total",
    "Consent records inserted via POST /me/consent (P0-5).",
    ["kind", "granted"],  # granted ∈ {"true", "false"}
    registry=REGISTRY,
)
# P0-6 — account self-service instrumentation. Each counter increments
# once per terminal route transition. Operators care about the rates here
# both as health signals (export job failures, deletion processed counts)
# and as cohort signals (how many users exercise the right to be forgotten).
email_change_requested_total = Counter(
    "account_email_change_requested_total",
    "POST /me/email/change requests that passed validation and queued a magic link.",
    registry=REGISTRY,
)
email_change_confirmed_total = Counter(
    "account_email_change_confirmed_total",
    "POST /me/email/confirm requests that successfully landed the new email.",
    registry=REGISTRY,
)
account_sign_out_all_total = Counter(
    "account_sign_out_all_total",
    "POST /me/sessions/sign-out-all requests that bumped the session epoch.",
    registry=REGISTRY,
)
data_exports_requested_total = Counter(
    "account_data_exports_requested_total",
    "Data-export rows transitioning into the labelled status.",
    # status ∈ {queued, ready, failed}. Incremented on initial enqueue
    # (queued) and on the worker's terminal transition (ready / failed).
    ["status"],
    registry=REGISTRY,
)
account_deletions_scheduled_total = Counter(
    "account_deletions_scheduled_total",
    "POST /me/delete requests that armed the 7-day grace timer.",
    registry=REGISTRY,
)
account_deletions_cancelled_total = Counter(
    "account_deletions_cancelled_total",
    "POST /me/delete/cancel requests that cleared a scheduled deletion.",
    registry=REGISTRY,
)
account_deletions_processed_total = Counter(
    "account_deletions_processed_total",
    "Accounts hard-deleted by the scheduled grace worker.",
    registry=REGISTRY,
)
# P1-1 — emits one tick per cron invocation of process_deletion_grace.py.
# ``result`` is one of {success, partial, failed}:
#   * ``success`` — every eligible row hard-deleted cleanly
#   * ``partial`` — at least one row processed, at least one raised
#   * ``failed``  — no rows processed (DB unreachable / import broke)
# Lets ops detect a wedged sweeper even when "0 rows" is the legitimate
# expected outcome on most days.
account_deletion_grace_run_total = Counter(
    "account_deletion_grace_run_total",
    "Invocations of the account-deletion grace sweeper (P1-1).",
    ["result"],  # result ∈ {"success", "partial", "failed"}
    registry=REGISTRY,
)
# P1-5 — incremented by DeletionLockMiddleware whenever a mutating request
# is rejected with 403 because the caller's account is mid-grace. Labelled
# by the request path so a sudden spike on one endpoint surfaces a FE
# regression (e.g. an /account page that retries past the lock).
deletion_lock_blocked_total = Counter(
    "deletion_lock_blocked_total",
    "Mutating requests blocked by the deletion-grace lockout middleware.",
    ["path"],
    registry=REGISTRY,
)
# P1-7 — incremented by consume_email_change_token on every early-return
# branch so ops can split benign replays (one user double-clicking the
# email) from suspicious patterns (unknown tokens, wrong-purpose tokens).
email_change_token_rejected_total = Counter(
    "email_change_token_rejected_total",
    "Email-change tokens that consume_email_change_token refused.",
    ["reason"],  # reason ∈ {"unknown", "already_used", "wrong_purpose", "expired"}
    registry=REGISTRY,
)
# P2 bundle — incremented by create_magic_link when a sign-in attempt is
# suppressed because the requested address is currently reserved as
# pending_email on another account (reverse-direction TOCTOU defence).
# Distinct from email_change_token_rejected_total because this fires on
# the request side, not on the consume side.
magic_link_suppressed_total = Counter(
    "magic_link_suppressed_total",
    "Magic-link requests suppressed before any token was minted.",
    ["reason"],  # reason ∈ {"pending_email_in_flight"} today; extensible.
    registry=REGISTRY,
)


def metrics_asgi_app():
    """Return the ASGI app that exposes the registry on /metrics."""
    return make_asgi_app(registry=REGISTRY)


# ---------------------------------------------------------------------------
# Structured logging + redaction
# ---------------------------------------------------------------------------

# Field names whose *values* must never appear in production logs.
_REDACT_FIELDS = frozenset(
    {
        "email",
        "token",
        "cookie",
        "api_key",
        "password",
        "secret",
        "prompt",
        "user_prompt",
        "agent_response",
    }
)

_REDACTED = "[REDACTED]"

# Regex pairs applied to the fully-rendered ``record["message"]`` string so
# even positional-argument log calls (``logger.info("for {}", email)``)
# don't leak PII / bearer credentials. The order matters: token redaction
# runs first so an email-like token query value gets masked as a token, not
# accidentally re-classified as an email.
_TOKEN_QUERY_RE = re.compile(r"(?i)(token=)[^&\s]+")
_EMAIL_LIKE_RE = re.compile(
    # Tight enough to avoid catching version strings ("v1.2.3@host") while
    # still matching every realistic RFC-5322 local-part the magic-link
    # flow accepts (letters, digits, plus/period/hyphen/underscore).
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
)


def _scrub_message(text: str) -> str:
    """Strip ``?token=...`` values and email-shaped tokens from ``text``.

    Applied to every log line before it leaves the process so a positional
    interpolation of an email or a magic-link URL never lands in the log
    aggregator verbatim. Idempotent: re-running on already-redacted text is
    a no-op (the placeholders themselves never match the regexes).
    """
    if not text:
        return text
    text = _TOKEN_QUERY_RE.sub(r"\1[redacted]", text)
    text = _EMAIL_LIKE_RE.sub("[redacted-email]", text)
    return text


def _redact(obj: Any, depth: int = 0) -> Any:
    """Walk dicts/lists/tuples, masking values for sensitive keys.

    Bounded depth keeps a hostile log payload from blowing the stack.
    """
    if depth > 6:
        return obj
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if isinstance(k, str) and k.lower() in _REDACT_FIELDS:
                out[k] = _REDACTED
            else:
                out[k] = _redact(v, depth + 1)
        return out
    if isinstance(obj, list):
        return [_redact(v, depth + 1) for v in obj]
    if isinstance(obj, tuple):
        return tuple(_redact(v, depth + 1) for v in obj)
    return obj


def _redact_filter(record: Any) -> bool:
    """Loguru filter: redact sensitive fields + scrub the rendered message.

    Returns True so the record continues through the pipeline.

    Two scrubs run here so a positional log argument can't bypass the
    structured-field redaction:

      1. ``record["extra"]`` — sensitive dict / list fields by key name.
      2. ``record["message"]`` — regex pass over the formatted message
         text to strip ``?token=...`` query values and email-shaped
         tokens. This catches the common pattern ``logger.info("for {}",
         email)`` where the email is interpolated positionally and
         therefore never lands in ``extra``.

    ``record`` is loguru's ``Record`` type, which behaves like a mapping. We
    accept ``Any`` here because loguru's typed signature uses an internal
    ``Record`` TypedDict and not a generic ``dict``.
    """
    extra = record.get("extra")
    if isinstance(extra, dict) and extra:
        record["extra"] = _redact(extra)
    message = record.get("message")
    if isinstance(message, str) and message:
        record["message"] = _scrub_message(message)
    return True


def _json_format(record: Any) -> str:
    """Loguru ``format=`` callback that emits a single redacted JSON line.

    We pre-redact ``record['extra']`` so the resulting JSON never carries a
    sensitive field. The trailing ``{exception}`` placeholder lets loguru
    attach its own multi-line traceback if one is associated with the record.
    """
    extra = record.get("extra") or {}
    if extra:
        record["extra"] = _redact(extra)
    message = record.get("message")
    if isinstance(message, str):
        record["message"] = _scrub_message(message)
    payload = {
        "timestamp": record["time"].isoformat(),
        "level": record["level"].name,
        "logger": record["name"],
        "function": record["function"],
        "line": record["line"],
        "message": record["message"],
    }
    # Promote ``request_id`` to a top-level field (when present) so log
    # aggregators can index on it directly instead of digging into ``extra``.
    # Bound by RequestIdMiddleware via ``logger.contextualize`` — see
    # app/middleware/request_id.py.
    record_extra = record.get("extra") or {}
    if isinstance(record_extra, dict):
        request_id = record_extra.get("request_id")
        if request_id:
            payload["request_id"] = request_id
    if record_extra:
        payload["extra"] = record_extra
    exc = record.get("exception")
    if exc is not None:
        payload["exception"] = {
            "type": getattr(exc.type, "__name__", str(exc.type)) if exc.type else None,
            "value": str(exc.value) if exc.value is not None else None,
        }
    return json.dumps(payload, default=str) + "\n"


def _json_sink(message: Any) -> None:
    """Legacy sink kept for back-compat with anything importing it directly."""
    sys.stderr.write(_json_format(message.record))


def configure_logging(level: str = "INFO") -> None:
    """Configure loguru — JSON in non-dev environments, pretty in dev.

    Importing here (not at module top) keeps this cheap when callers
    only need the metric symbols.
    """
    # Local import avoids a circular import between config and observability
    # at module load (config has no observability dep — but tests that mock
    # settings sometimes evaluate config lazily).
    from app.config import get_settings

    settings = get_settings()
    logger.remove()

    if settings.arena_env == "development":
        logger.add(
            sys.stderr,
            level=level.upper(),
            format=(
                "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
                "<level>{level: <8}</level> | "
                "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
                "<level>{message}</level>"
            ),
            backtrace=False,
            diagnose=False,
            filter=_redact_filter,
        )
    else:
        # Production / staging / test → structured JSON with redaction.
        logger.add(
            _json_sink,
            level=level.upper(),
            backtrace=False,
            diagnose=False,
            filter=_redact_filter,
        )

    # Side-effect: opportunistically wire OTel auto-instrumentation if
    # configured. Failures are non-fatal — the API still boots without OTel.
    _maybe_configure_otel(settings)


def _maybe_configure_otel(settings: Any) -> None:
    """Install FastAPI + SQLAlchemy auto-instrumentation when configured."""
    endpoint = getattr(settings, "otel_exporter_otlp_endpoint", None)
    if not endpoint:
        return
    try:
        # Imports are intentionally lazy so the OTel SDK is a soft dependency.
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except Exception as exc:  # pragma: no cover — exercised only when OTel installed
        logger.warning("OTel SDK not importable, skipping instrumentation: {}", exc)
        return

    resource = Resource.create(
        {
            "service.name": "agentarena-api",
            "service.namespace": "agentarena",
            "deployment.environment": getattr(settings, "arena_env", "unknown"),
        }
    )
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)

    # Try the FastAPI + SQLAlchemy instrumentors; each is optional independently.
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor().instrument()
    except Exception as exc:  # pragma: no cover
        logger.debug("FastAPI OTel instrumentor unavailable: {}", exc)

    try:
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

        SQLAlchemyInstrumentor().instrument()
    except Exception as exc:  # pragma: no cover
        logger.debug("SQLAlchemy OTel instrumentor unavailable: {}", exc)

    logger.info("OpenTelemetry instrumentation enabled → {}", endpoint)
