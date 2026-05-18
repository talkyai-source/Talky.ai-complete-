"""Provider cost ledger (Phase 4.2).

Records every chargeable provider event into
``tenant_provider_cost_events``. The recorder is **fire-and-forget**
on the hot path: callers call ``record(...)`` which returns
immediately, and a single background flusher batches inserts to keep
the call's critical path latency-clean.

Two modes:

  - **Online** — backed by Postgres + asyncpg. Used in production. The
    flusher runs every ``FLUSH_INTERVAL_S`` seconds and writes any
    buffered events in a single ``COPY`` (asyncpg's
    ``copy_records_to_table``) for throughput. On DB failure events
    stay buffered up to ``MAX_BUFFER_SIZE``; older ones get dropped
    and a warning logged.

  - **Disabled** — when ``COST_LEDGER_ENABLED=false`` or no DB pool
    is reachable. ``record()`` becomes a no-op. The voice pipeline
    never observes the difference.

Per-provider extraction lives in tiny ``parse_*`` helpers — each
takes the response object/headers a provider client already has, and
returns a list of ``CostEvent``. This keeps the per-call hot-path
edits to a single one-line ``ledger.record(...)`` per provider.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class CostEvent:
    tenant_id: str
    provider: str
    provider_role: str       # "llm" | "tts" | "stt"
    unit: str
    quantity: float
    call_id: Optional[str] = None
    api_key_fp: Optional[str] = None
    unit_price_usd: Optional[float] = None
    cost_usd: Optional[float] = None
    model: Optional[str] = None
    voice_id: Optional[str] = None
    latency_ms: Optional[int] = None
    status: str = "ok"
    occurred_at_ts: float = field(default_factory=time.time)


def redact_key_fp(api_key: Optional[str]) -> Optional[str]:
    """First 4 + last 4 of an API key — same shape as KeyPool.redacted()."""
    if not api_key:
        return None
    if len(api_key) <= 8:
        return api_key[:2] + "***"
    return f"{api_key[:4]}…{api_key[-4:]}"


# In-process buffer + flusher.
_buffer: list[CostEvent] = []
_buffer_lock = asyncio.Lock()
_flusher_task: Optional[asyncio.Task] = None
_stop_event: Optional[asyncio.Event] = None

FLUSH_INTERVAL_S = float(os.getenv("COST_LEDGER_FLUSH_INTERVAL_S", "5.0"))
MAX_BUFFER_SIZE = int(os.getenv("COST_LEDGER_MAX_BUFFER", "5000"))


def is_enabled() -> bool:
    return (os.getenv("COST_LEDGER_ENABLED", "true").lower()
            in ("1", "true", "yes"))


def record(event: CostEvent) -> None:
    """Append an event to the in-process buffer. Safe to call from
    sync code; never blocks. Drops the oldest event when the buffer
    is full so a stalled DB doesn't memory-leak."""
    if not is_enabled():
        return
    if len(_buffer) >= MAX_BUFFER_SIZE:
        # Drop oldest. Logs every 100 drops to avoid a flood.
        dropped = _buffer.pop(0)
        if (len(_buffer) % 100) == 0:
            logger.warning(
                "cost_ledger_buffer_overflow tenant=%s provider=%s — dropping oldest",
                dropped.tenant_id, dropped.provider,
            )
    _buffer.append(event)


async def _flush_once(pool: Any) -> int:
    """Flush the buffer once. Returns the number of rows written."""
    async with _buffer_lock:
        if not _buffer:
            return 0
        batch = list(_buffer)
        _buffer.clear()

    if pool is None:
        # No DB — keep the events in memory for the next flush cycle.
        async with _buffer_lock:
            _buffer.extend(batch)
        return 0

    try:
        rows = [
            (
                e.tenant_id, e.call_id,
                e.provider, e.provider_role, e.api_key_fp,
                e.unit, e.quantity, e.unit_price_usd, e.cost_usd,
                e.model, e.voice_id,
                e.latency_ms, e.status,
            )
            for e in batch
        ]
        async with pool.acquire() as conn:
            # RLS bypass — the recorder is platform-internal and may
            # write rows for any tenant.
            await conn.execute("SET LOCAL app.bypass_rls = 'true'")
            await conn.copy_records_to_table(
                "tenant_provider_cost_events",
                records=rows,
                columns=[
                    "tenant_id", "call_id",
                    "provider", "provider_role", "api_key_fp",
                    "unit", "quantity", "unit_price_usd", "cost_usd",
                    "model", "voice_id",
                    "latency_ms", "status",
                ],
            )
        logger.debug("cost_ledger_flushed rows=%d", len(rows))
        return len(rows)
    except Exception as exc:
        # On DB failure, requeue the batch so we try again next tick.
        # Cap at MAX_BUFFER_SIZE to avoid unbounded growth.
        async with _buffer_lock:
            if len(_buffer) + len(batch) <= MAX_BUFFER_SIZE:
                _buffer[:0] = batch
        logger.warning("cost_ledger_flush_failed err=%s", exc)
        return 0


async def _flusher_loop(pool_provider) -> None:
    """Background task: flush every FLUSH_INTERVAL_S until stopped."""
    assert _stop_event is not None
    while not _stop_event.is_set():
        try:
            await asyncio.wait_for(
                _stop_event.wait(), timeout=FLUSH_INTERVAL_S,
            )
        except asyncio.TimeoutError:
            pass
        try:
            pool = pool_provider() if callable(pool_provider) else pool_provider
        except Exception:
            pool = None
        await _flush_once(pool)


async def start_flusher(pool_provider) -> None:
    """Start the background flusher.

    `pool_provider` is either an asyncpg pool or a zero-arg callable
    returning one (so the lookup is deferred until DB is up)."""
    global _flusher_task, _stop_event
    if not is_enabled():
        logger.info("cost_ledger_disabled")
        return
    if _flusher_task is not None and not _flusher_task.done():
        return  # already running
    _stop_event = asyncio.Event()
    _flusher_task = asyncio.create_task(_flusher_loop(pool_provider))
    logger.info("cost_ledger_started flush_interval_s=%.1f", FLUSH_INTERVAL_S)


async def stop_flusher() -> None:
    """Stop the flusher and drain the buffer one last time."""
    global _flusher_task, _stop_event
    if _stop_event is not None:
        _stop_event.set()
    if _flusher_task is not None:
        try:
            await asyncio.wait_for(_flusher_task, timeout=FLUSH_INTERVAL_S * 2)
        except (asyncio.TimeoutError, Exception):
            _flusher_task.cancel()
    _flusher_task = None
    _stop_event = None


def buffer_size() -> int:
    """Diagnostic: how many events are pending. Used by the readiness
    snapshot so saturation is visible."""
    return len(_buffer)


# ──────────────────────────────────────────────────────────────────
# Per-provider quantity extractors.
#
# Each extractor returns a partial CostEvent (or None) so the caller
# only fills in tenant_id / call_id / api_key_fp at the call site.
# Unit prices are NOT computed here — that's a separate `apply_pricing`
# pass run periodically by an ops job (sample query in the migration
# comments). Snapshots stay aligned with whatever pricing rev applies.
# ──────────────────────────────────────────────────────────────────

def _read_field(obj: Any, name: str) -> Any:
    """Pull a field from either a mapping or an attribute object."""
    if obj is None:
        return None
    val = getattr(obj, name, None)
    if val is not None:
        return val
    if hasattr(obj, "get"):
        return obj.get(name)
    return None


def parse_groq_usage(response_usage: Any) -> list[CostEvent]:
    """Groq returns a `usage` block on completion: prompt_tokens +
    completion_tokens. Generates two events (one per unit)."""
    if not response_usage:
        return []
    out: list[CostEvent] = []
    pt = _read_field(response_usage, "prompt_tokens")
    ct = _read_field(response_usage, "completion_tokens")
    if pt:
        out.append(CostEvent(
            tenant_id="", provider="groq", provider_role="llm",
            unit="tokens_in", quantity=float(pt),
        ))
    if ct:
        out.append(CostEvent(
            tenant_id="", provider="groq", provider_role="llm",
            unit="tokens_out", quantity=float(ct),
        ))
    return out


def parse_elevenlabs_usage(text: str) -> list[CostEvent]:
    """ElevenLabs bills per character of synthesised text."""
    if not text:
        return []
    return [CostEvent(
        tenant_id="", provider="elevenlabs", provider_role="tts",
        unit="characters", quantity=float(len(text)),
    )]


def parse_cartesia_usage(text: str) -> list[CostEvent]:
    """Cartesia bills per character (similar to ElevenLabs)."""
    if not text:
        return []
    return [CostEvent(
        tenant_id="", provider="cartesia", provider_role="tts",
        unit="characters", quantity=float(len(text)),
    )]


def parse_deepgram_usage(audio_seconds: float) -> list[CostEvent]:
    """Deepgram bills per audio-second of streaming STT."""
    if audio_seconds <= 0:
        return []
    return [CostEvent(
        tenant_id="", provider="deepgram", provider_role="stt",
        unit="audio_seconds", quantity=float(audio_seconds),
    )]
