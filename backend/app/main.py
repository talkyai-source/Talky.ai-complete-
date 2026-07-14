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


async def _event_loop_lag_heartbeat(stop_event: asyncio.Event) -> None:
    """Per-loop scheduling-lag heartbeat — the primary "knee" metric.

    Wakes every 10ms on an ABSOLUTE deadline and records how far past that
    deadline the loop actually scheduled it. Absolute deadlines (not chained
    ``sleep(0.010)`` calls) so measurement error cannot accumulate as drift.
    Under CPU contention / a blocking call this lag climbs long before per-turn
    latency degrades, which makes it the metric to watch during load testing
    and a standing production saturation signal.

    Independent of ASYNCIO_DEBUG / slow_callback diagnostics. Fail-soft: a
    metrics hiccup re-arms the deadline instead of killing the loop; cancelled
    cleanly on shutdown via ``stop_event`` (same path as the other background
    tasks).
    """
    from app.infrastructure.metrics.voice_metrics import (
        observe_event_loop_lag_seconds,
    )

    loop = asyncio.get_running_loop()
    period = 0.010
    deadline = loop.time() + period
    while not stop_event.is_set():
        try:
            await asyncio.sleep(max(0.0, deadline - loop.time()))
            now = loop.time()
            lag = max(0.0, now - deadline)
            observe_event_loop_lag_seconds(lag)
            deadline += period
            if now > deadline:
                # Still behind after advancing by one period means the stall
                # already recorded above (lag > one period) ate into more
                # than one tick. Without this resync, the un-advanced
                # deadline stays in the past and the next several iterations
                # would each sleep(0) and emit their OWN lag sample for the
                # very same stall — a catch-up burst that over-represents one
                # event as many. Resync onto "now" so a single stall always
                # yields exactly one observation. The normal (non-stall) case
                # never hits this branch, so chained absolute deadlines keep
                # scheduling with no drift.
                deadline = now + period
        except asyncio.CancelledError:
            raise
        except Exception:
            # Never let a metric error kill the heartbeat. Re-arm off "now"
            # so a long stall doesn't spend the next iterations in a burst of
            # zero-length sleeps trying to catch a stale deadline up.
            deadline = loop.time() + period


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

    # ── 4. Per-tenant AI-config DB lookup wiring ──────────────────
    # Per-call provider SELECTION (LLM model/provider/temperature/max-tokens,
    # STT engine, TTS, pipeline mode, realtime settings) is resolved per-tenant
    # from tenant_ai_configs at call time via TenantAIConfigResolver — keyed on
    # the call's own tenant_id (campaign.tenant_id outbound, dialed DID inbound).
    #
    # This REPLACES the old boot-restore that loaded "whichever tenant saved
    # last" into a process-global and used it as everyone's default — that was
    # the source of cross-tenant model bleed. There is intentionally no global
    # restore anymore: tenant-less paths (Ask AI, browser tests) use the
    # immutable code default; every real call resolves its own tenant's row.
    try:
        from app.domain.services.tenant_ai_config_resolver import (
            get_tenant_ai_config_resolver,
        )
        from app.api.v1.endpoints.ai_options._shared import _fetch_tenant_config

        _ai_cfg_pool = getattr(container, "db_pool", None)
        if _ai_cfg_pool is not None:
            async def _tenant_ai_config_db_lookup(tenant_id: str):
                # One indexed lookup on tenant_ai_configs, cache-bypassed by
                # design so an AI-Options edit lands on the tenant's next call
                # without a restart. Bypass-RLS inline: runs from a non-request
                # context (no per-tenant session active). Returns an
                # AIProviderConfig or None (no row → resolver uses the default).
                async with _ai_cfg_pool.acquire() as conn:
                    async with conn.transaction():
                        await conn.execute("SET LOCAL app.bypass_rls = 'true'")
                        return await _fetch_tenant_config(conn, tenant_id)

            get_tenant_ai_config_resolver().set_db_lookup(_tenant_ai_config_db_lookup)
            logger.info("tenant_ai_config_db_lookup_wired")
        else:
            logger.info(
                "tenant_ai_config_db_lookup_skipped reason=no_db_pool "
                "— resolver running on process default only"
            )
    except Exception as exc:  # noqa: BLE001 — AI-config lookup never blocks startup
        logger.warning(
            "tenant_ai_config_db_lookup_wiring_failed err=%s "
            "— resolver falls back to process default",
            exc,
        )

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
    # Use the dedicated pub/sub client (no request-path read timeout) so the
    # blocking listen() loops don't thrash-reconnect every socket_timeout.
    redis_for_listeners = getattr(container, "redis_pubsub", None) or getattr(container, "redis", None)
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

    # Event-loop scheduling-lag heartbeat. A 10ms absolute-deadline ticker
    # that records how far past each deadline the loop woke it — the primary
    # "knee" metric for load testing and a standing production saturation
    # signal. Runs regardless of Redis/DB (it measures THIS loop), never blocks
    # startup (fire-and-forget create_task), and is drained by the same
    # stop-event / cancel path as the listeners above. Guarded so a single
    # process/loop never registers two heartbeats.
    if not getattr(app.state, "_loop_lag_heartbeat_started", False):
        app.state._loop_lag_heartbeat_started = True
        app.state.redis_listener_tasks.append(
            asyncio.create_task(
                _event_loop_lag_heartbeat(app.state.redis_listener_stop)
            )
        )
        logger.info("event_loop_lag_heartbeat_started period_ms=10")

    # Single-owner telephony lock. Exactly ONE process may hold the ARI
    # event connection to Asterisk and serve calls — all per-call live
    # state (VoiceSession, WebSockets, asyncio tasks) is process-local
    # and cannot be shared. We claim the lock BEFORE connecting ARI: only
    # the winner connects; a loser (a stray second worker / bad deploy /
    # --workers >1) skips ARI and 503s telephony routes instead of
    # silently splitting calls across processes. On the in-memory backend
    # this always returns True (single process), so behaviour is
    # unchanged when TELEPHONY_STATE_BACKEND=memory.
    from app.infrastructure.telephony.adapter_factory import CallControlAdapterFactory
    from app.api.v1.endpoints import telephony_bridge as _tb
    from app.domain.services.telephony.state_backend import get_state_backend

    _state_backend = get_state_backend()
    try:
        _is_owner = await _state_backend.acquire_telephony_ownership()
    except Exception as e:
        # acquire is meant to fail open; if it somehow raises, default to
        # owning (single-worker is the norm) so we never self-inflict an
        # outage. The lock still protects against the multi-worker case
        # whenever Redis is reachable.
        logger.warning(f"Telephony ownership acquire raised (assuming owner): {e}")
        _is_owner = True

    # Heartbeat renews both this process's liveness marker and the owner
    # lock; start it regardless of role (harmless for a non-owner) so an
    # owner's lock never lapses under a live call. No-op on memory backend.
    try:
        await _state_backend.start_heartbeat()
    except Exception as e:
        logger.warning(f"Telephony heartbeat start failed (non-fatal): {e}")

    async def _auto_connect_telephony() -> None:
        """Connect the bridge to Asterisk and wire the FULL callback set.

        Shared by the boot path and the stale-lock retry below. Wires the
        same callbacks as the manual /sip/telephony/connect endpoint —
        the boot path previously skipped the ringing/early-ringing/alias
        hooks, so a boot-connected process silently lost ring-time warmup
        and live ringing status until someone manually hit /connect.
        """
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
            if hasattr(_tb._adapter, "set_ringing_callback"):
                _tb._adapter.set_ringing_callback(_tb._on_ringing)
            if hasattr(_tb._adapter, "set_early_ringing_callback"):
                _tb._adapter.set_early_ringing_callback(_tb._on_early_ringing)
            if hasattr(_tb._adapter, "set_outbound_channel_alias_callback"):
                _tb._adapter.set_outbound_channel_alias_callback(_tb._alias_ringing_call_id)
            await _tb._adapter.connect()
            logger.info(f"Telephony bridge auto-connected: {_tb._adapter.name}")
        else:
            logger.info("Telephony bridge already connected — skipping auto-connect")
        # Arm the inactivity watchdog + pod-capacity readiness wiring.
        # Without this, a normal lifespan boot left the capacity gate and
        # the zombie-session watchdog disarmed — only a manual POST to
        # /sip/telephony/start turned them on. (audit #9)
        _tb.ensure_session_management_started()

    if not _is_owner:
        owner = None
        try:
            owner = await _state_backend.telephony_owner_id()
        except Exception:
            pass
        logger.critical(
            "TELEPHONY NOT ACTIVE on this process — the ARI owner lock is held "
            "by %s. This process will NOT connect ARI and will 503 telephony "
            "routes. Expected with --workers >1 / a second pod; if you see this "
            "on a single-worker deploy, a previous owner's lock has not yet "
            "expired (clears within ~60s). Will retry ownership for ~5 minutes.",
            owner or "another process",
        )

        # On a single-worker deploy the usual cause is a RESTART: the old
        # process died holding the lock (expires ~60s), the new one tried
        # ONCE at boot and gave up — leaving telephony 503'ing until a
        # manual /connect or another restart (observed 2026-07-08: 6+ min
        # of failed originations after a deploy). Retry for a few minutes;
        # a genuine second worker keeps failing acquire and nothing changes.
        async def _retry_telephony_ownership() -> None:
            for attempt in range(1, 7):  # ~5 minutes of coverage
                await asyncio.sleep(50)
                try:
                    if await _state_backend.acquire_telephony_ownership():
                        logger.info(
                            "Telephony ownership acquired on retry %d — connecting ARI",
                            attempt,
                        )
                        await _auto_connect_telephony()
                        return
                except Exception as exc:  # noqa: BLE001 — keep retrying
                    logger.warning(
                        "telephony_ownership_retry_failed attempt=%d err=%s",
                        attempt, exc,
                    )
            logger.critical(
                "Telephony ownership NOT acquired after retries — this process "
                "will keep 503ing telephony routes (another owner is alive, or "
                "the lock backend is unhealthy)."
            )

        asyncio.get_running_loop().create_task(_retry_telephony_ownership())
    else:
        # Auto-connect telephony bridge so campaigns can originate calls
        # immediately. Must happen after container startup (needs the loop).
        try:
            await _auto_connect_telephony()
        except Exception as e:
            logger.warning(f"Telephony bridge auto-connect failed (non-fatal): {e}")

        # Phase 1 item 1 — telephony state recovery. Reclaim any calls a
        # dead predecessor left in the Redis ledger (hang them up + record
        # the terminal state). Only the owner does this — it's the only
        # process with an ARI connection to issue the hangups. No-op on the
        # in-memory backend. Best-effort — never block startup.
        try:
            from app.domain.services.telephony.lifecycle import recover_orphaned_calls
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
