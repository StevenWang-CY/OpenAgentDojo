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
    ["reason"],  # reason = no_redis | publish_error
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
    """Loguru filter: redact sensitive fields out of ``record['extra']``.

    Returns True so the record continues through the pipeline.

    ``record`` is loguru's ``Record`` type, which behaves like a mapping. We
    accept ``Any`` here because loguru's typed signature uses an internal
    ``Record`` TypedDict and not a generic ``dict``.
    """
    extra = record.get("extra")
    if isinstance(extra, dict) and extra:
        record["extra"] = _redact(extra)
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
    payload = {
        "timestamp": record["time"].isoformat(),
        "level": record["level"].name,
        "logger": record["name"],
        "function": record["function"],
        "line": record["line"],
        "message": record["message"],
    }
    if record.get("extra"):
        payload["extra"] = record["extra"]
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
