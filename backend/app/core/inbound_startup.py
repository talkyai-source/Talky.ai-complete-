"""Fail-closed production startup checks for the inbound telephony path."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def telephony_ownership_failure_is_fatal(strict_validation: bool, inbound_enabled: bool) -> bool:
    """Whether an unprovable telephony-ownership claim must refuse startup.

    Ownership only has to be PROVEN when production inbound is live: a second
    owner would split live calls across processes. An outbound-only production
    process must survive a transient state-backend (Redis) blip at boot — the
    acquire path is documented to fail OPEN there, and refusing to start turns
    a blip into a full API outage.
    """
    return bool(strict_validation and inbound_enabled)


def require_inbound_admission_watchdog(
    watchdog: Any, strict_validation: bool, inbound_enabled: bool
) -> None:
    """Fail closed when the inbound admission reconciler never started.

    Without it, durable inbound reservations are never reconciled and billed
    minutes / concurrency leases stay stranded. That is only fatal when
    production inbound is actually enabled; elsewhere it is a loud warning
    rather than the previous complete silence.
    """
    if watchdog is not None:
        return
    if telephony_ownership_failure_is_fatal(strict_validation, inbound_enabled):
        raise RuntimeError(
            "Inbound admission watchdog did not start (no database pool) — "
            "production inbound cannot reconcile durable reservations"
        )
    logger.warning(
        "inbound_admission_watchdog_not_started reason=no_db_pool "
        "— durable inbound reservations will not be reconciled"
    )


def is_production(environment: object) -> bool:
    return str(environment or "").strip().lower() == "production"


async def platform_inbound_enabled(db_pool: Any, *, environment: object) -> bool:
    """Read the durable global inbound switch before connecting telephony.

    Production must be able to prove this state.  A missing/unreachable table
    is an ambiguous safety state, not permission to auto-connect a fallback
    adapter.
    """

    if not is_production(environment):
        return False
    if db_pool is None:
        raise RuntimeError("Cannot validate production inbound state without a DB pool")
    try:
        async with db_pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SET LOCAL app.bypass_rls = 'true'")
                value = await conn.fetchval(
                    "SELECT inbound_enabled FROM platform_runtime_controls WHERE id=1"
                )
    except Exception as exc:
        raise RuntimeError("Cannot validate the production platform inbound switch") from exc
    return bool(value)


async def validate_production_inbound_database_role(
    database: Any,
    *,
    environment: object,
    inbound_enabled: bool,
) -> None:
    """Require a non-superuser, non-BYPASSRLS role before live inbound.

    The canonical tenant policies are ineffective for superusers and roles
    carrying ``BYPASSRLS``. Accept either an asyncpg connection (for an
    existing control transaction) or a pool (for startup/manual connect).
    """

    if not is_production(environment) or not inbound_enabled:
        return
    if database is None:
        raise RuntimeError("Production inbound cannot validate the database runtime role")

    async def _read_role(conn: Any) -> Any:
        return await conn.fetchrow(
            """
            SELECT current_user AS role_name, rolsuper, rolbypassrls
            FROM pg_roles
            WHERE rolname=current_user
            """
        )

    try:
        if callable(getattr(database, "fetchrow", None)):
            role = await _read_role(database)
        else:
            async with database.acquire() as conn:
                role = await _read_role(conn)
    except Exception as exc:
        raise RuntimeError("Production inbound cannot validate the database runtime role") from exc

    if not role:
        raise RuntimeError("Production inbound database role was not found")
    if bool(role["rolsuper"]) or bool(role["rolbypassrls"]):
        role_name = str(role["role_name"] or "unknown")
        raise RuntimeError(
            "Production inbound requires a NOSUPERUSER NOBYPASSRLS database "
            f"role; current role {role_name!r} bypasses tenant isolation"
        )


def validate_production_inbound_adapter(
    *,
    environment: object,
    inbound_enabled: bool,
    configured_adapter: object,
    adapter: Any,
) -> None:
    """Prove the enabled production path is the admission-aware Asterisk adapter."""

    if not is_production(environment) or not inbound_enabled:
        return
    configured = str(configured_adapter or "auto").strip().lower()
    if configured != "asterisk":
        raise RuntimeError(
            "Production inbound requires TELEPHONY_ADAPTER=asterisk; "
            "auto-detection and FreeSWITCH fallback are not admitted"
        )
    implementation = (type(adapter).__module__, type(adapter).__name__)
    if implementation != (
        "app.infrastructure.telephony.asterisk_adapter",
        "AsteriskAdapter",
    ):
        raise RuntimeError("Production inbound resolved a non-Asterisk adapter")
    required_callbacks = (
        "set_inbound_admission_callback",
        "set_inbound_answered_persist_callback",
        "set_inbound_terminal_proof_persist_callback",
        "set_inbound_admission_finalizer",
        "list_recoverable_application_channel_ids",
        "recovery_excluded_channel_ids",
        "hangup_many_confirmed",
    )
    missing = [name for name in required_callbacks if not callable(getattr(adapter, name, None))]
    if missing:
        raise RuntimeError(
            "Production inbound adapter lacks required callbacks: " + ", ".join(missing)
        )


def validate_live_production_inbound_adapter(
    *,
    environment: object,
    inbound_enabled: bool,
    configured_adapter: object,
    adapter: Any,
) -> None:
    """Apply the adapter contract and require an already-connected instance."""

    validate_production_inbound_adapter(
        environment=environment,
        inbound_enabled=inbound_enabled,
        configured_adapter=configured_adapter,
        adapter=adapter,
    )
    if is_production(environment) and inbound_enabled:
        if adapter is None or not bool(getattr(adapter, "connected", False)):
            raise RuntimeError(
                "Production inbound requires a live admission-aware Asterisk adapter"
            )
        required_wiring = (
            "_on_inbound_admission",
            "_on_inbound_answered_persist",
            "_on_inbound_terminal_proof_persist",
            "_on_inbound_admission_finalize",
        )
        unwired = [name for name in required_wiring if not callable(getattr(adapter, name, None))]
        if unwired:
            raise RuntimeError(
                "Production inbound adapter callbacks are not wired: " + ", ".join(unwired)
            )


def validate_production_inbound_state_backend(
    *,
    environment: object,
    inbound_enabled: bool,
    configured_backend: object,
    state_backend: Any,
) -> None:
    """Enabled production inbound requires real cross-process coordination."""

    if not is_production(environment) or not inbound_enabled:
        return
    configured = str(configured_backend or "memory").strip().lower()
    if configured != "redis":
        raise RuntimeError("Production inbound requires TELEPHONY_STATE_BACKEND=redis")
    implementation = (type(state_backend).__module__, type(state_backend).__name__)
    if implementation != (
        "app.domain.services.telephony.state_backend",
        "RedisBackedStateBackend",
    ):
        raise RuntimeError("Production inbound Redis state backend fell back to local memory")
    required_capabilities = (
        "acquire_telephony_ownership_strict",
        "start_heartbeat_strict",
        "register_cleanup_obligation",
        "claim_cleanup_obligation_if_absent",
        "register_answer_intent_cleanup_obligation",
        "promote_answered_cleanup_obligation",
    )
    missing = [
        name
        for name in required_capabilities
        if not callable(getattr(state_backend, name, None))
    ]
    if missing:
        raise RuntimeError(
            "Production inbound state backend lacks strict capabilities: "
            + ", ".join(missing)
        )
