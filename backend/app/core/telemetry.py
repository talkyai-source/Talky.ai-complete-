"""
app/core/telemetry.py — OpenTelemetry distributed tracing for Talky.ai

Instruments:
  - FastAPI HTTP requests (automatic via FastAPIInstrumentor)
  - asyncpg database queries (automatic via AsyncPGInstrumentor)
  - Redis calls (automatic via RedisInstrumentor)
  - Voice pipeline spans (manual — STT / LLM / TTS / telephony)
  - WebSocket connections (manual helper)

Configuration via environment variables:
  OTEL_ENABLED           = true/false (default: true in production)
  OTEL_SERVICE_NAME      = talky-backend (default)
  OTEL_EXPORTER_ENDPOINT = http://localhost:4317 (OTLP gRPC endpoint)
                           Leave empty to export to console (dev mode)
  OTEL_ENVIRONMENT       = production / staging / development

Usage — in main.py lifespan startup:
  from app.core.telemetry import setup_telemetry, shutdown_telemetry
  setup_telemetry(app)          # call before app is serving requests
  # in shutdown:
  shutdown_telemetry()
"""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Any, Generator, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Feature flag — skip silently if deps are not installed
# ---------------------------------------------------------------------------
try:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import (
        BatchSpanProcessor,
        ConsoleSpanExporter,
        SimpleSpanProcessor,
    )
    from opentelemetry.sdk.resources import Resource, SERVICE_NAME
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor
    from opentelemetry.instrumentation.redis import RedisInstrumentor
    _OTEL_AVAILABLE = True
except ImportError:
    _OTEL_AVAILABLE = False
    logger.warning(
        "OpenTelemetry packages not installed — tracing disabled. "
        "Run: pip install opentelemetry-instrumentation-fastapi "
        "opentelemetry-instrumentation-asyncpg "
        "opentelemetry-instrumentation-redis "
        "opentelemetry-exporter-otlp-proto-grpc"
    )

_tracer: Optional[Any] = None
_provider: Optional[Any] = None


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def setup_telemetry(app: Any) -> None:
    """
    Initialise OpenTelemetry and instrument the FastAPI app.
    Call once during lifespan startup before serving requests.
    """
    global _tracer, _provider

    enabled = os.getenv("OTEL_ENABLED", "true").lower() in ("true", "1", "yes")
    if not enabled or not _OTEL_AVAILABLE:
        logger.info("OpenTelemetry tracing: disabled")
        return

    service_name = os.getenv("OTEL_SERVICE_NAME", "talky-backend")
    environment  = os.getenv("OTEL_ENVIRONMENT", os.getenv("ENVIRONMENT", "production"))
    endpoint     = os.getenv("OTEL_EXPORTER_ENDPOINT", "")  # empty = console

    resource = Resource(attributes={
        SERVICE_NAME: service_name,
        "deployment.environment": environment,
        "service.version": os.getenv("APP_VERSION", "1.0.0"),
    })

    _provider = TracerProvider(resource=resource)

    if endpoint:
        # Production: export to Grafana Tempo, Jaeger, or any OTLP-compatible backend
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
            exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
            _provider.add_span_processor(BatchSpanProcessor(exporter))
            logger.info(f"OTel tracing → OTLP gRPC at {endpoint}")
        except ImportError:
            logger.warning(
                "opentelemetry-exporter-otlp-proto-grpc not installed. "
                "Falling back to console exporter."
            )
            _provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    else:
        # Development: print spans to stdout
        _provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
        logger.info("OTel tracing → console (set OTEL_EXPORTER_ENDPOINT for production)")

    trace.set_tracer_provider(_provider)
    _tracer = trace.get_tracer(service_name)

    # Auto-instrument FastAPI — adds a span for every HTTP request
    FastAPIInstrumentor.instrument_app(
        app,
        excluded_urls="health,metrics",  # skip health/metrics polls
    )

    # Auto-instrument asyncpg — adds a span for every DB query
    AsyncPGInstrumentor().instrument()

    # Auto-instrument Redis — adds a span for every Redis command
    RedisInstrumentor().instrument()

    logger.info(
        f"OpenTelemetry tracing enabled — service={service_name} env={environment}"
    )


def shutdown_telemetry() -> None:
    """Flush all pending spans and shut down the provider."""
    global _provider
    if _provider and _OTEL_AVAILABLE:
        try:
            _provider.shutdown()
            logger.info("OpenTelemetry provider shut down cleanly")
        except Exception as exc:
            logger.error(f"OTel shutdown error: {exc}")


def get_tracer() -> Any:
    """Return the configured tracer (or a no-op tracer if OTel is disabled)."""
    if _tracer is not None:
        return _tracer
    if _OTEL_AVAILABLE:
        return trace.get_tracer("talky-backend")
    return _NoOpTracer()


# ---------------------------------------------------------------------------
# Manual span helpers for the voice pipeline
# ---------------------------------------------------------------------------

@contextmanager
def voice_span(
    name: str,
    call_id: str,
    tenant_id: Optional[str] = None,
    **extra_attrs: Any,
) -> Generator[Any, None, None]:
    """
    Context manager for a voice pipeline span.

    Usage:
        with voice_span("stt.transcribe", call_id=call_id, provider="deepgram") as span:
            result = await deepgram.transcribe(audio)
            span.set_attribute("stt.words", len(result.words))

    Automatically records:
      - voice.call_id
      - voice.tenant_id (if provided)
      - Any extra keyword arguments as span attributes
      - Exception details if the block raises
    """
    tracer = get_tracer()
    if not _OTEL_AVAILABLE or isinstance(tracer, _NoOpTracer):
        yield _NoOpSpan()
        return

    with tracer.start_as_current_span(f"voice.{name}") as span:
        span.set_attribute("voice.call_id", call_id)
        if tenant_id:
            span.set_attribute("voice.tenant_id", tenant_id)
        for k, v in extra_attrs.items():
            try:
                span.set_attribute(k, v)
            except Exception:
                pass
        try:
            yield span
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(
                trace.StatusCode.ERROR if _OTEL_AVAILABLE else None,  # type: ignore
                str(exc),
            )
            raise


@contextmanager
def pipeline_span(
    stage: str,
    call_id: str,
    provider: str,
    tenant_id: Optional[str] = None,
) -> Generator[Any, None, None]:
    """
    Convenience wrapper for the STT → LLM → TTS pipeline stages.

    Usage:
        with pipeline_span("stt", call_id, provider="deepgram"):
            transcript = await stt.transcribe(audio)

        with pipeline_span("llm", call_id, provider="groq"):
            response = await llm.generate(transcript)

        with pipeline_span("tts", call_id, provider="cartesia"):
            audio = await tts.synthesize(response)
    """
    with voice_span(
        stage,
        call_id=call_id,
        tenant_id=tenant_id,
        **{f"voice.{stage}.provider": provider},
    ) as span:
        yield span


def record_latency(span: Any, stage: str, latency_ms: float) -> None:
    """Attach a latency measurement to an existing span."""
    if span and _OTEL_AVAILABLE and not isinstance(span, _NoOpSpan):
        try:
            span.set_attribute(f"voice.{stage}.latency_ms", round(latency_ms, 1))
        except Exception:
            pass


# ---------------------------------------------------------------------------
# No-op fallbacks when OTel is not installed
# ---------------------------------------------------------------------------

class _NoOpSpan:
    def set_attribute(self, *a: Any, **kw: Any) -> None: pass
    def record_exception(self, *a: Any, **kw: Any) -> None: pass
    def set_status(self, *a: Any, **kw: Any) -> None: pass
    def __enter__(self): return self
    def __exit__(self, *a): pass


class _NoOpTracer:
    @contextmanager
    def start_as_current_span(self, name: str, **kw: Any):
        yield _NoOpSpan()
