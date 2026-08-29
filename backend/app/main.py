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
    from app.utils import event_loop_lag as _lag_view

    loop = asyncio.get_running_loop()
    period = 0.010
    deadline = loop.time() + period
    while not stop_event.is_set():
        try:
            await asyncio.sleep(max(0.0, deadline - loop.time()))
            now = loop.time()
            lag = max(0.0, now - deadline)
            observe_event_loop_lag_seconds(lag)
            # Same observation, published for SYNCHRONOUS readers. The
            # histogram answers "how has the loop been lately"; it cannot
            # answer "was the loop stalled when THIS audio batch arrived
            # late", which needs a value in hand at the moment of the log
            # call. telephony_audio_gap reads it — see event_loop_lag.
            _lag_view.record(lag)
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


async def _terminate_active_telephony_sessions_for_shutdown(
    state_backend,
    adapter=None,
    *,
    deadline_s: float = 5.5,
) -> dict[str, object]:
    """Request proof-aware PBX teardown for every session left after drain.

    Returns an honest total/confirmed/deferred summary. A deferred call is intentionally left
    in both local state and the durable Redis ledger; clearing this process's
    heartbeat later in shutdown makes it immediately recoverable by the next
    telephony owner. Browser/non-PBX callers keep their existing behavior via
    the helper's default argument, while shutdown explicitly requires a PBX
    acknowledgement before logical settlement.
    """
    from app.domain.services.telephony.lifecycle import _force_end_and_hangup
    from app.domain.services.telephony.termination import request_confirmed_hangup

    session_ids = {
        str(call_id)
        for call_id, _voice_session in state_backend.iter_voice_session_items()
        if call_id
    }
    if adapter is None:
        from app.api.v1.endpoints import telephony_bridge as _shutdown_tb

        adapter = _shutdown_tb._adapter
    owned_ids: set[str] = set()
    owned_snapshot = getattr(adapter, "owned_call_ids", None)
    if callable(owned_snapshot):
        try:
            owned_ids = {str(value) for value in owned_snapshot() if value}
        except Exception as exc:
            logger.warning("Shutdown: adapter ownership snapshot failed: %s", exc)
    call_ids = sorted(session_ids | owned_ids)
    if not call_ids:
        return {
            "total": 0,
            "attempted": 0,
            "confirmed": 0,
            "deferred": 0,
            "deferred_call_ids": [],
        }

    logger.info(
        "Shutdown: requesting confirmed teardown for %d locally-owned "
        "telephony call root(s) (drain expired)",
        len(call_ids),
    )

    async def terminate_one(call_id: str) -> bool:
        try:
            if call_id in session_ids:
                return bool(
                    await _force_end_and_hangup(
                        call_id,
                        require_confirmation=True,
                    )
                )
            proof = await request_confirmed_hangup(adapter, call_id)
            return bool(proof.confirmed)
        except asyncio.CancelledError:
            raise
        except Exception as shutdown_err:
            logger.warning(
                "Shutdown: error requesting confirmed teardown for call %s: %s",
                call_id[:12],
                shutdown_err,
            )
            return False

    task_to_call = {
        asyncio.create_task(
            terminate_one(call_id),
            name=f"telephony-shutdown:{call_id}",
        ): call_id
        for call_id in call_ids
    }
    done, pending = await asyncio.wait(
        task_to_call,
        timeout=max(0.1, min(10.0, float(deadline_s))),
    )
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)

    confirmed_ids: set[str] = set()
    for task in done:
        try:
            if task.result():
                confirmed_ids.add(task_to_call[task])
        except (asyncio.CancelledError, Exception):
            pass
    deferred_ids = sorted(set(call_ids) - confirmed_ids)
    for call_id in deferred_ids:
        if call_id in session_ids:
            logger.warning(
                "Shutdown: call %s has no PBX termination proof; preserving "
                "its session ledger for successor recovery",
                call_id[:12],
            )
    return {
        "total": len(call_ids),
        "attempted": len(task_to_call),
        "confirmed": len(confirmed_ids),
        "deferred": len(deferred_ids),
        "deferred_call_ids": deferred_ids,
    }


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
    strict_validation = environment.strip().lower() == "production"

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
            "asyncio_debug_enabled slow_callback_threshold_s=%.2f",
            slow_threshold,
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
                        await conn.execute("SET LOCAL app.bypass_rls = 'true'")
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
                "voice_tuning_db_lookup_skipped reason=no_db_pool " "— resolver running env-only"
            )
    except Exception as exc:  # noqa: BLE001 — voice tuning never blocks startup
        logger.warning(
            "voice_tuning_db_lookup_wiring_failed err=%s " "— resolver falls back to env+defaults",
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

    # Durable inbound reservations need an application-scoped reconciler.
    # The watchdog is deliberately independent of the telephony ownership
    # lock: every pass is idempotent and database-serialized, so a dead owner
    # cannot strand billed minutes or concurrency leases indefinitely.
    app.state.inbound_admission_watchdog = None
    _inbound_pool = getattr(container, "db_pool", None)
    if _inbound_pool is not None:
        try:
            from app.domain.services.telephony.inbound_admission import (
                start_inbound_admission_watchdog,
            )

            app.state.inbound_admission_watchdog = start_inbound_admission_watchdog(
                _inbound_pool,
                interval_seconds=60,
                max_age_seconds=7200,
                batch_limit=100,
            )
            logger.info("inbound_admission_watchdog_started")
        except Exception as exc:
            logger.error("inbound_admission_watchdog_start_failed err=%s", exc)
            if strict_validation:
                raise

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

    # ── 2b. Prompt version archive (goals.md §6 rollback) ─────────
    # Capture this build's persona bodies so an earlier version can be rolled
    # back to without a redeploy, then load them into the sync cache the
    # (synchronous) session-config builder reads. Both are best-effort: a
    # failure here costs the ability to roll back, never the ability to call.
    try:
        from app.services.scripts.prompts.bodies import (
            load_cache as _load_prompt_bodies,
            record_current_versions as _record_prompt_versions,
        )

        _pool = getattr(container, "db_pool", None)
        _new = await _record_prompt_versions(_pool)
        _cached = await _load_prompt_bodies(_pool)
        logger.info("prompt_version_archive newly_recorded=%d cached=%d", _new, _cached)
        app.state.prompt_versions_cached = _cached
    except Exception as exc:  # noqa: BLE001
        logger.warning("prompt_version_archive_failed err=%s", exc)

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
    redis_for_listeners = getattr(container, "redis_pubsub", None) or getattr(
        container, "redis", None
    )
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
                cleanup_expired_events_loop(_events_pool, stop_event=app.state.redis_listener_stop)
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
            asyncio.create_task(_event_loop_lag_heartbeat(app.state.redis_listener_stop))
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
    from app.core.inbound_startup import (
        platform_inbound_enabled,
        require_inbound_admission_watchdog,
        telephony_ownership_failure_is_fatal,
        validate_production_inbound_database_role,
        validate_production_inbound_adapter,
        validate_live_production_inbound_adapter,
        validate_production_inbound_state_backend,
    )

    _production_inbound_enabled = await platform_inbound_enabled(
        getattr(container, "db_pool", None), environment=environment
    )
    await validate_production_inbound_database_role(
        getattr(container, "db_pool", None),
        environment=environment,
        inbound_enabled=_production_inbound_enabled,
    )

    # A falsy db_pool skipped the inbound admission watchdog above in silence.
    # Now that inbound status is known, refuse to serve production inbound
    # without its reconciler (and say so loudly everywhere else).
    require_inbound_admission_watchdog(
        app.state.inbound_admission_watchdog,
        strict_validation,
        _production_inbound_enabled,
    )

    _state_backend = get_state_backend()
    validate_production_inbound_state_backend(
        environment=environment,
        inbound_enabled=_production_inbound_enabled,
        configured_backend=os.getenv("TELEPHONY_STATE_BACKEND", "memory"),
        state_backend=_state_backend,
    )
    try:
        if strict_validation and _production_inbound_enabled:
            _is_owner = await _state_backend.acquire_telephony_ownership_strict()
        else:
            _is_owner = await _state_backend.acquire_telephony_ownership()
    except Exception as e:
        if telephony_ownership_failure_is_fatal(strict_validation, _production_inbound_enabled):
            logger.critical(
                "telephony_ownership_ambiguous production startup refused err_type=%s",
                type(e).__name__,
            )
            raise RuntimeError("Production telephony ownership could not be proven") from e
        logger.warning(f"Telephony ownership acquire raised (assuming owner in dev): {e}")
        _is_owner = True

    # Heartbeat renews both this process's liveness marker and the owner
    # lock; start it regardless of role (harmless for a non-owner) so an
    # owner's lock never lapses under a live call. No-op on memory backend.
    try:
        if strict_validation and _production_inbound_enabled:
            await _state_backend.start_heartbeat_strict()
        else:
            await _state_backend.start_heartbeat()
    except Exception as e:
        if _is_owner and telephony_ownership_failure_is_fatal(
            strict_validation, _production_inbound_enabled
        ):
            raise RuntimeError("Production telephony ownership heartbeat could not start") from e
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
            validate_production_inbound_adapter(
                environment=environment,
                inbound_enabled=_production_inbound_enabled,
                configured_adapter=adapter_type,
                adapter=_tb._adapter,
            )
            _tb._adapter.register_call_event_handlers(
                on_new_call=_tb._on_new_call,
                on_call_ended=_tb._on_call_ended,
                on_audio_received=_tb._on_audio_received,
            )
            if hasattr(_tb._adapter, "set_inbound_admission_callback"):
                _tb._adapter.set_inbound_admission_callback(_tb._admit_inbound_call)
            if hasattr(_tb._adapter, "set_inbound_answered_persist_callback"):
                _tb._adapter.set_inbound_answered_persist_callback(_tb._persist_inbound_answered)
            if hasattr(_tb._adapter, "set_inbound_admission_finalizer"):
                _tb._adapter.set_inbound_admission_finalizer(_tb._finalize_inbound_admission)
            if hasattr(_tb._adapter, "set_transfer_connected_callback"):
                _tb._adapter.set_transfer_connected_callback(_tb._on_transfer_connected)
            if hasattr(
                _tb._adapter,
                "set_transfer_answered_persist_callback",
            ):
                _tb._adapter.set_transfer_answered_persist_callback(
                    _tb._on_transfer_answered_persisted
                )
            if hasattr(
                _tb._adapter,
                "set_transfer_cleanup_confirmed_callback",
            ):
                _tb._adapter.set_transfer_cleanup_confirmed_callback(
                    _tb._on_transfer_cleanup_confirmed
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
        # Validate both freshly-created and already-connected adapters.  This
        # prevents an earlier/manual connection from bypassing the startup
        # contract simply because the creation branch was skipped.
        validate_live_production_inbound_adapter(
            environment=environment,
            inbound_enabled=_production_inbound_enabled,
            configured_adapter=os.getenv("TELEPHONY_ADAPTER", "auto"),
            adapter=_tb._adapter,
        )
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
                    if strict_validation and _production_inbound_enabled:
                        acquired = await _state_backend.acquire_telephony_ownership_strict()
                    else:
                        acquired = await _state_backend.acquire_telephony_ownership()
                    if acquired:
                        if strict_validation and _production_inbound_enabled:
                            await _state_backend.start_heartbeat_strict()
                        else:
                            await _state_backend.start_heartbeat()
                        logger.info(
                            "Telephony ownership acquired on retry %d — connecting ARI",
                            attempt,
                        )
                        await _auto_connect_telephony()
                        # Delayed ownership is a real takeover boundary too.
                        # Run recovery immediately; waiting for the next
                        # watchdog tick leaves stale linked PSTN legs live for
                        # up to another interval after ARI becomes available.
                        try:
                            from app.domain.services.telephony.lifecycle import (
                                recover_orphaned_calls,
                            )

                            recovered = await recover_orphaned_calls()
                            if recovered:
                                logger.info(
                                    "telephony delayed-owner recovery: reclaimed %d orphaned call(s)",
                                    recovered,
                                )
                        except Exception as recovery_exc:
                            logger.warning(
                                "Telephony delayed-owner recovery failed "
                                "(watchdog will retry): %s",
                                recovery_exc,
                            )
                        return
                except Exception as exc:  # noqa: BLE001 — keep retrying
                    logger.warning(
                        "telephony_ownership_retry_failed attempt=%d err=%s",
                        attempt,
                        exc,
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
            if strict_validation and _production_inbound_enabled:
                raise
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
        _sb.voice_session_count(),
        _readiness.DRAIN_TIMEOUT_S,
    )
    drain_deadline = asyncio.get_event_loop().time() + _readiness.DRAIN_TIMEOUT_S
    while _sb.voice_session_count() > 0 and asyncio.get_event_loop().time() < drain_deadline:
        await asyncio.sleep(2.0)
        logger.info(
            "lifespan_drain_wait active=%d elapsed_s=%.1f",
            _sb.voice_session_count(),
            _readiness.drain_seconds_elapsed(),
        )

    # Request PBX termination before logical teardown/settlement. Any channel
    # whose teardown cannot be proved stays in the Redis ledger; shutdown later
    # clears this incarnation's heartbeat so the successor retries it
    # immediately. Calling ``_on_call_ended`` directly here used to release
    # reservations while the carrier leg could still be live and billable.
    shutdown_termination = await _terminate_active_telephony_sessions_for_shutdown(
        _sb,
        _tb._adapter,
    )
    logger.info(
        "telephony_shutdown_termination total=%d confirmed=%d deferred=%d",
        shutdown_termination["total"],
        shutdown_termination["confirmed"],
        shutdown_termination["deferred"],
    )

    if _tb._adapter and _tb._adapter.connected:
        try:
            await _tb._adapter.disconnect(
                drain_timeout_s=5.5,
                force_handoff=True,
            )
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
                *app.state.redis_listener_tasks,
                return_exceptions=True,
            )
        logger.info("redis_coordination_listeners_stopped")
    except Exception as exc:
        logger.warning("redis_listener_shutdown_raised err=%s", exc)

    # ── Shutdown ──────────────────────────────────────────────────
    logger.info("Shutting down Talky.ai...")
    try:
        _inbound_watchdog = getattr(app.state, "inbound_admission_watchdog", None)
        if _inbound_watchdog is not None:
            await _inbound_watchdog.stop()
            logger.info("inbound_admission_watchdog_stopped")
    except Exception as exc:
        logger.warning("inbound_admission_watchdog_stop_failed err=%s", exc)
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
