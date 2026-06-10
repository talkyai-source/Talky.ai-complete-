"""
FastAPI Application Entry Point
"""
import asyncio
import os
import logging
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI

from app.core.dotenv_compat import load_dotenv

# Load backend .env regardless of current working directory.
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(dotenv_path=_BACKEND_ROOT / ".env", override=False)

from app.api.v1.routes import api_router
from app.api.operational import (
    health_check,
    prometheus_metrics,
    register_operational_routes,
    root,
)
from app.core.app_bootstrap import configure_logging, configure_middleware
from app.core.config import ConfigManager, get_settings

# ── Logging ──────────────────────────────────────────────────────
configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan - startup and shutdown events.

    Startup order:
      1. OpenTelemetry (must come first — instruments everything below)
      2. ServiceContainer (Redis, PostgreSQL, Queue, Sessions)
      3. Provider configuration validation

    Shutdown order (reverse):
      3. ServiceContainer graceful drain
      2. OTel flush (must come last — ensures all spans are exported)
    """
    from app.core.container import get_container
    from app.core.prod_gate import enforce_production_gate
    from app.core.sentry_init import init_sentry
    from app.core.telemetry import setup_telemetry, shutdown_telemetry

    environment = os.getenv("ENVIRONMENT", "development")
    strict_validation = environment == "production"

    # ── 0. Production gate (T0.2 + T0.3) ─────────────────────────
    # Refuse to boot in production if any obvious fatal misconfig is
    # present — dev-bypass flags still set, default PBX passwords,
    # missing JWT_SECRET, mock-mode billing, etc. Fail LOUD before the
    # service container brings Redis/DB up; no silent "mostly working"
    # production deploys.
    enforce_production_gate()

    # ── 0.5. Sentry (T2.3) ───────────────────────────────────────
    # Before FastAPI middleware / OTEL so Sentry's integrations see
    # every request. No-op when SENTRY_DSN is unset.
    init_sentry()

    # ── 1. OpenTelemetry ─────────────────────────────────────────
    # Must be set up BEFORE the container so that asyncpg and Redis
    # auto-instrumentation patches are in place before first use.
    setup_telemetry(app)

    # ── 1.5 Phase 1.5 — blocking I/O detector ────────────────────
    # When ASYNCIO_DEBUG=1 the event loop logs any callback that
    # runs longer than ASYNCIO_SLOW_CALLBACK_S (default 0.1s).
    # In a voice pipeline, anything blocking the loop for >100ms
    # means audio frames are being dropped — surface it loudly so
    # CI/staging catches it before production. Off by default in
    # production for cost; staging operators set the env var.
    if os.getenv("ASYNCIO_DEBUG", "").lower() in ("1", "true", "yes"):
        loop = asyncio.get_event_loop()
        loop.set_debug(True)
        slow_threshold = float(os.getenv("ASYNCIO_SLOW_CALLBACK_S", "0.1"))
        loop.slow_callback_duration = slow_threshold
        logger.warning(
            "asyncio_debug_enabled slow_callback_threshold_s=%.2f", slow_threshold,
        )

    # ── 2. Service container ──────────────────────────────────────
    logger.info("Starting Talky.ai AI Voice Dialer...")
    container = get_container()
    try:
        await container.startup()
        app.state.container = container
    except Exception as e:
        if strict_validation:
            logger.error(f"Container startup failed: {e}")
            raise
        logger.warning(f"Container startup warning: {e}")

    # ── 2.4. Voice-tuning DB lookup wiring (T4-C3) ───────────────
    # The VoiceTuningResolver supports an async DB lookup so per-tenant
    # tuning persists in tenant_ai_configs.voice_tuning. Wire the hook
    # at startup; tests and dev runs without a DB pool fall back to
    # env-only resolution. Bypass-RLS is set inline because the lookup
    # runs from a non-request context (no per-tenant session active).
    try:
        from app.domain.services.voice_tuning import (
            get_voice_tuning_resolver,
        )
        _voice_tuning_pool = getattr(container, "db_pool", None)

        if _voice_tuning_pool is not None:
            async def _voice_tuning_db_lookup(tenant_id: str):
                # One indexed lookup; cache-bypassed by design so UI
                # edits land on the next call without a restart.
                async with _voice_tuning_pool.acquire() as conn:
                    async with conn.transaction():
                        await conn.execute(
                            "SET LOCAL app.bypass_rls = 'true'"
                        )
                        row = await conn.fetchrow(
                            "SELECT voice_tuning FROM tenant_ai_configs "
                            "WHERE tenant_id = $1::uuid",
                            tenant_id,
                        )
                if row is None:
                    return None
                raw = row["voice_tuning"]
                if isinstance(raw, str):
                    import json as _json
                    try:
                        raw = _json.loads(raw)
                    except (ValueError, TypeError):
                        return None
                if isinstance(raw, dict) and raw:
                    return raw
                return None

            get_voice_tuning_resolver().set_db_lookup(_voice_tuning_db_lookup)
            logger.info("voice_tuning_db_lookup_wired")
        else:
            logger.info(
                "voice_tuning_db_lookup_skipped reason=no_db_pool "
                "— resolver running env-only"
            )
    except Exception as exc:  # noqa: BLE001 — voice tuning never blocks startup
        logger.warning(
            "voice_tuning_db_lookup_wiring_failed err=%s "
            "— resolver falls back to env+defaults",
            exc,
        )

    # ── 2.5. Redis durability probe (T2.4) ──────────────────────
    # Loud WARN in prod when both AOF and RDB are off — dialer jobs
    # would vanish on any Redis restart. Non-fatal: an operator might
    # intentionally be running a cache-only Redis, in which case they
    # can set the env.
    try:
        from app.core.redis_durability import probe_redis_durability
        redis_client = getattr(container, "redis", None)
        durability = await probe_redis_durability(redis_client)
        app.state.redis_durability = durability
    except Exception as exc:
        logger.warning("redis_durability_probe_raised err=%s", exc)

    # ── 2.6. Legacy-campaign audit (T2.6) ───────────────────────
    # Count campaigns still falling through to the hardcoded
    # estimation prompt. Loud WARN in prod when any are present so
    # operators can migrate before we delete the fallback.
    try:
        from app.core.legacy_campaign_audit import (
            audit_legacy_campaigns,
            log_audit_summary,
        )
        result = await audit_legacy_campaigns(getattr(container, "db_pool", None))
        log_audit_summary(result)
        app.state.legacy_campaign_audit = result
    except Exception as exc:
        logger.debug("legacy_campaign_audit_raised err=%s", exc)

    # ── 3. Provider validation ────────────────────────────────────
    try:
        from app.core.validation import validate_providers_on_startup
        validate_providers_on_startup(strict=strict_validation)
    except RuntimeError as e:
        if strict_validation:
            logger.error(f"Provider validation failed: {e}")
            raise
        logger.warning(f"Configuration warnings (non-fatal in {environment}): {e}")

    logger.info("Talky.ai started successfully")

    # ── 4. Restore saved AI config ────────────────────────────────
    # Load the most-recently saved tenant config from DB so the global AI
    # config (TTS provider, voice, LLM model) survives server restarts and
    # hot-reloads without requiring the user to re-visit AI Options first.
    try:
        from app.domain.services.global_ai_config import set_global_config
        from app.domain.models.ai_config import AIProviderConfig
        db_client = container.db_client
        async with db_client.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT llm_provider, llm_model, llm_temperature, llm_max_tokens,
                       stt_provider, stt_model, stt_language,
                       tts_provider, tts_model, tts_voice_id, tts_sample_rate
                FROM tenant_ai_configs
                ORDER BY updated_at DESC NULLS LAST
                LIMIT 1
                """
            )
        if row:
            saved = AIProviderConfig(
                llm_provider=row["llm_provider"],
                llm_model=row["llm_model"],
                llm_temperature=row["llm_temperature"],
                llm_max_tokens=row["llm_max_tokens"],
                stt_provider=row["stt_provider"],
                stt_model=row["stt_model"],
                stt_language=row["stt_language"],
                tts_provider=row["tts_provider"],
                tts_model=row["tts_model"],
                tts_voice_id=row["tts_voice_id"],
                tts_sample_rate=row["tts_sample_rate"],
            )
            set_global_config(saved)
            logger.info(
                "AI config restored from DB: tts=%s voice=%s llm=%s",
                saved.tts_provider, saved.tts_voice_id, saved.llm_model,
            )
    except Exception as exc:
        logger.warning("Could not restore AI config from DB (using defaults): %s", exc)

    # ── Phase 4.2 — provider cost ledger flusher ────────────────
    # Records per-call provider cost events and batches them into
    # tenant_provider_cost_events every COST_LEDGER_FLUSH_INTERVAL_S.
    # No-op when COST_LEDGER_ENABLED=false.
    try:
        from app.domain.services import provider_cost_ledger as _ledger
        await _ledger.start_flusher(lambda: getattr(container, "db_pool", None))
    except Exception as exc:
        logger.warning("cost_ledger_start_failed err=%s", exc)

    # ── Phase 2.2 — cross-pod Redis coordination listeners ──────
    # Two long-lived tasks per pod:
    #  • keyspace_expiry_listener: reaps the active-call set the
    #    instant a lease key TTLs out (crashed pod / hung call).
    #  • quota_alerts_listener: caches the latest tenant throttle
    #    decision so make_call doesn't DB-read on the hot path.
    # Both are best-effort: if Redis is unavailable they no-op.
    redis_for_listeners = getattr(container, "redis", None)
    app.state.redis_listener_stop = asyncio.Event()
    app.state.redis_listener_tasks = []
    if redis_for_listeners is not None:
        from app.domain.services.global_concurrency_listener import (
            keyspace_expiry_listener,
            quota_alerts_listener,
        )
        app.state.redis_listener_tasks = [
            asyncio.create_task(
                keyspace_expiry_listener(
                    redis_for_listeners,
                    stop_event=app.state.redis_listener_stop,
                )
            ),
            asyncio.create_task(
                quota_alerts_listener(
                    redis_for_listeners,
                    stop_event=app.state.redis_listener_stop,
                )
            ),
        ]
        logger.info("redis_coordination_listeners_started count=2")

    # Periodic stream_events cleanup — the table's rows expire (expires_at
    # default now()+90d) but nothing deleted them, so it grew forever and slowed
    # the /events poll. Reuses the listener stop-event for clean shutdown.
    _events_pool = getattr(container, "db_pool", None)
    if _events_pool is not None:
        from app.domain.services.event_emitter import cleanup_expired_events_loop
        app.state.redis_listener_tasks.append(
            asyncio.create_task(
                cleanup_expired_events_loop(
                    _events_pool, stop_event=app.state.redis_listener_stop
                )
            )
        )
        logger.info("stream_events_cleanup_task_started")

    # Auto-connect telephony bridge so campaigns can originate calls immediately.
    # Must happen after container startup (needs event loop to be running).
    from app.infrastructure.telephony.adapter_factory import CallControlAdapterFactory
    from app.api.v1.endpoints import telephony_bridge as _tb

    try:
        if not (_tb._adapter and _tb._adapter.connected):
            adapter_type = os.getenv("TELEPHONY_ADAPTER", "auto")
            _tb._adapter = await CallControlAdapterFactory.create(adapter_type)
            _tb._adapter.register_call_event_handlers(
                on_new_call=_tb._on_new_call,
                on_call_ended=_tb._on_call_ended,
                on_audio_received=_tb._on_audio_received,
            )
            if hasattr(_tb._adapter, "set_global_session_start_callback"):
                _tb._adapter.set_global_session_start_callback(_tb._on_ws_session_start)
            await _tb._adapter.connect()
            logger.info(f"Telephony bridge auto-connected: {_tb._adapter.name}")
        else:
            logger.info("Telephony bridge already connected — skipping auto-connect")
    except Exception as e:
        logger.warning(f"Telephony bridge auto-connect failed (non-fatal): {e}")

    # Phase 1 item 1 — telephony state recovery. Start this process's
    # heartbeat, then reclaim any calls a dead predecessor left in the
    # Redis ledger (hang them up + record the terminal state). Both are
    # no-ops on the in-memory backend, so this is safe regardless of
    # TELEPHONY_STATE_BACKEND. Best-effort — never block startup.
    try:
        from app.domain.services.telephony.state_backend import get_state_backend
        from app.domain.services.telephony.lifecycle import recover_orphaned_calls
        await get_state_backend().start_heartbeat()
        recovered = await recover_orphaned_calls()
        if recovered:
            logger.info("telephony startup recovery: reclaimed %d orphaned call(s)", recovered)
    except Exception as e:
        logger.warning(f"Telephony state recovery failed (non-fatal): {e}")

    yield

    # Phase 1.4 — flip readiness to NOT_READY immediately so the load
    # balancer stops sending new calls. Existing calls finish; the loop
    # below waits up to DRAIN_TIMEOUT_S for natural completion before
    # forcing teardown.
    from app.core import readiness as _readiness
    from app.domain.services.telephony.state_backend import get_state_backend as _get_sb
    _sb = _get_sb()
    _readiness.begin_drain()
    logger.info(
        "lifespan_drain_begin active=%d timeout_s=%d",
        _sb.voice_session_count(), _readiness.DRAIN_TIMEOUT_S,
    )
    drain_deadline = asyncio.get_event_loop().time() + _readiness.DRAIN_TIMEOUT_S
    while (
        _sb.voice_session_count() > 0
        and asyncio.get_event_loop().time() < drain_deadline
    ):
        await asyncio.sleep(2.0)
        logger.info(
            "lifespan_drain_wait active=%d elapsed_s=%.1f",
            _sb.voice_session_count(),
            _readiness.drain_seconds_elapsed(),
        )

    # Disconnect telephony bridge on shutdown.
    # FIX 5 — End active voice sessions first so recordings are saved and the PBX
    # receives a hangup signal.  Without this, callers hear abrupt disconnect and
    # the PBX holds channels open until its own ringing/idle timeout.
    _remaining = _sb.iter_voice_session_items()
    if _remaining:
        logger.info(
            "Shutdown: ending %d active telephony session(s) (drain expired)",
            len(_remaining),
        )
        for call_id, _vs in _remaining:
            try:
                await _tb._on_call_ended(call_id)
            except Exception as shutdown_err:
                logger.warning(
                    "Shutdown: error ending call %s: %s", call_id[:12], shutdown_err
                )

    if _tb._adapter and _tb._adapter.connected:
        try:
            await _tb._adapter.disconnect()
            _tb._adapter = None
            logger.info("Telephony bridge disconnected")
        except Exception as e:
            logger.error(f"Error disconnecting telephony bridge: {e}")

    # Phase 1 item 1 — stop the heartbeat and clear it so the successor
    # process recovers our calls immediately rather than waiting for the
    # heartbeat TTL to lapse. No-op on the in-memory backend.
    try:
        await _sb.shutdown()
    except Exception as e:
        logger.warning(f"Telephony state backend shutdown failed (non-fatal): {e}")

    # Phase 4.2 — flush + stop the cost ledger.
    try:
        from app.domain.services import provider_cost_ledger as _ledger
        await _ledger.stop_flusher()
    except Exception as exc:
        logger.warning("cost_ledger_stop_failed err=%s", exc)

    # Phase 2.2 — stop Redis coordination listeners cleanly.
    try:
        if getattr(app.state, "redis_listener_stop", None):
            app.state.redis_listener_stop.set()
        for t in getattr(app.state, "redis_listener_tasks", []):
            if not t.done():
                t.cancel()
        if getattr(app.state, "redis_listener_tasks", None):
            await asyncio.gather(
                *app.state.redis_listener_tasks, return_exceptions=True,
            )
        logger.info("redis_coordination_listeners_stopped")
    except Exception as exc:
        logger.warning("redis_listener_shutdown_raised err=%s", exc)

    # ── Shutdown ──────────────────────────────────────────────────
    logger.info("Shutting down Talky.ai...")
    try:
        await container.shutdown()
    except Exception as e:
        logger.error(f"Error during shutdown: {e}")

    # Flush all pending OTel spans before the process exits
    shutdown_telemetry()
    logger.info("Talky.ai shutdown complete")


_settings_for_app = get_settings()
_is_prod = (_settings_for_app.environment or "").lower() == "production"

# Vuln-fix 2026-05-21: lock the FastAPI auto-generated docs to non-prod.
# In production /docs, /redoc, /openapi.json hand any visitor a complete
# machine-readable map of every endpoint, parameter, and schema — that's
# the recon phase done for them. We keep the docs available on
# staging/dev where they're genuinely useful.
app = FastAPI(
    title="Talky.ai — AI Voice Dialer",
    description="Intelligent voice communication platform with AI agents",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None if _is_prod else "/docs",
    redoc_url=None if _is_prod else "/redoc",
    openapi_url=None if _is_prod else "/openapi.json",
)

# ── Middleware stack (order matters — outermost first) ────────────
configure_middleware(app)

# ── Routes ────────────────────────────────────────────────────────
app.include_router(api_router, prefix="/api/v1")
register_operational_routes(app)


if __name__ == "__main__":
    import uvicorn
    websocket_config = ConfigManager().get_websocket_config()
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        ws="websockets",
        ws_ping_interval=float(websocket_config.get("heartbeat_interval_seconds", 30)),
        ws_ping_timeout=float(websocket_config.get("heartbeat_timeout_seconds", 5)),
    )
