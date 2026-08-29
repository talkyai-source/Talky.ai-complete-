"""Tenant-safe lifecycle and administration for inbound campaigns.

The service is deliberately SQL-first.  Routing changes are security-sensitive
and the database constraints in migration 0022 are the final authority for
version conflicts, duplicate DIDs and cross-tenant ownership.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import asyncpg

from app.core.db_utils import acquire_with_tenant
from app.domain.services.telephony.business_hours import evaluate_business_hours
from app.domain.services.telephony.inbound_overrides import validate_qualification_overrides
from app.domain.services.telephony.inbound_router import (
    is_active_inbound_campaign_status,
    normalize_did,
    redact_did,
)
from app.domain.services.telephony.trunk_runtime import evaluate_trunk_runtime

logger = logging.getLogger(__name__)


class InboundCampaignError(Exception):
    """Base error carrying a stable API code and HTTP status."""

    def __init__(self, message: str, *, code: str = "inbound_error", status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class InboundNotFoundError(InboundCampaignError):
    def __init__(self, message: str = "Inbound campaign not found"):
        super().__init__(message, code="not_found", status_code=404)


class InboundConflictError(InboundCampaignError):
    def __init__(self, message: str, *, code: str = "version_conflict"):
        super().__init__(message, code=code, status_code=409)


class InboundReadinessError(InboundConflictError):
    def __init__(self, readiness: dict[str, Any]):
        super().__init__("Inbound campaign is not ready to activate", code="not_ready")
        self.readiness = readiness


@dataclass(frozen=True)
class ReadinessCheck:
    key: str
    label: str
    passed: bool
    detail: str


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (uuid.UUID, datetime, date, Decimal)):
        return str(value)
    return value


def _json_obj(value: Any, default: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, dict) else (default or {})
        except (TypeError, ValueError):
            return default or {}
    return default or {}


def _uuid(value: str, field: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise InboundCampaignError(
            f"{field} must be a valid UUID", code="invalid_identifier", status_code=422
        ) from exc


def _timezone(value: str) -> str:
    cleaned = str(value or "").strip()
    try:
        ZoneInfo(cleaned)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise InboundCampaignError(
            f"Unknown IANA timezone: {cleaned}", code="invalid_timezone", status_code=422
        ) from exc
    return cleaned


def _stable_hash(payload: Mapping[str, Any]) -> str:
    body = json.dumps(_json_safe(dict(payload)), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _config_checksum(payload: Mapping[str, Any]) -> str:
    allowed = {
        key: payload.get(key)
        for key in (
            "name",
            "campaign_id",
            "opening_mode",
            "greeting",
            "timezone",
            "business_hours",
            "after_hours_action",
            "transfer_number",
            "recording_enabled",
            "consent_message",
            "recording_policy",
            "transfer_policy",
            "qualification_config",
        )
    }
    return _stable_hash(allowed)


def _transfer_requested(payload: Mapping[str, Any]) -> bool:
    """Return whether a saved campaign configuration requests transfer."""

    return payload.get("after_hours_action") == "transfer" or (
        _json_obj(payload.get("transfer_policy")).get("enabled") is True
    )


def _require_transfer_runtime(payload: Mapping[str, Any]) -> None:
    """Block new/persisted transfer policy while the runtime gate is closed."""

    if not _transfer_requested(payload):
        return
    from app.domain.services.telephony.inbound_transfer import (
        inbound_transfer_runtime_available,
    )

    if not inbound_transfer_runtime_available():
        raise InboundConflictError(
            "Inbound transfer cannot be configured until the controlled "
            "linked-leg runtime is available in the approved environment",
            code="transfer_runtime_unavailable",
        )


async def _require_transfer_configuration(
    conn: Any,
    payload: Mapping[str, Any],
    *,
    tenant_id: str,
    config_id: Optional[str],
) -> None:
    """Require both independent gates before persisting transfer capability."""

    if not _transfer_requested(payload):
        return
    _require_transfer_runtime(payload)
    from app.domain.services.telephony.inbound_transfer import (
        inbound_transfer_scope_available,
    )

    if not inbound_transfer_scope_available(
        tenant_id=tenant_id,
        config_id=config_id,
    ):
        raise InboundConflictError(
            "Inbound transfer can only be configured for the exact campaign "
            "approved for the staging proof window",
            code="transfer_staging_scope_mismatch",
        )
    platform_enabled = bool(
        await conn.fetchval(
            "SELECT inbound_transfer_enabled FROM platform_runtime_controls WHERE id=1"
        )
    )
    if not platform_enabled:
        raise InboundConflictError(
            "Inbound transfer cannot be configured while the platform transfer "
            "kill switch is disabled",
            code="transfer_platform_disabled",
        )


def mask_did(value: Optional[str]) -> str:
    normalized = normalize_did(value)
    if not normalized:
        return "unknown"
    digits = normalized[1:]
    visible = digits[-4:] if len(digits) >= 4 else digits
    return f"+{'*' * max(len(digits) - len(visible), 3)}{visible}"


_BUNDLE_SQL = """
    SELECT
        cfg.id AS config_id,
        cfg.tenant_id,
        cfg.campaign_id,
        cfg.name,
        cfg.status AS config_status,
        cfg.version AS config_version,
        cfg.opening_mode,
        cfg.greeting,
        cfg.timezone,
        cfg.business_hours,
        cfg.after_hours_action,
        cfg.transfer_number,
        cfg.recording_enabled,
        cfg.consent_message,
        cfg.recording_policy,
        cfg.transfer_policy,
        cfg.qualification_config,
        cfg.config_checksum,
        cfg.active_at,
        cfg.created_at,
        cfg.updated_at,
        c.name AS campaign_name,
        c.description AS campaign_description,
        c.status AS campaign_status,
        c.direction AS campaign_direction,
        c.system_prompt,
        c.voice_id,
        c.tts_provider,
        c.goal,
        c.script_config,
        c.calling_config,
        t.business_name AS tenant_name,
        t.status AS tenant_status,
        t.subscription_status,
        t.minutes_allocated,
        COALESCE(tic.inbound_enabled, FALSE) AS tenant_inbound_enabled,
        ai.id AS tenant_ai_config_id,
        ai.llm_provider,
        ai.llm_model,
        ai.stt_provider,
        ai.stt_model,
        ai.tts_provider AS tenant_tts_provider,
        ai.tts_model AS tenant_tts_model,
        ai.tts_voice_id AS tenant_tts_voice_id,
        ai.pipeline_mode AS tenant_pipeline_mode,
        EXISTS (
            SELECT 1
            FROM tenant_telephony_concurrency_policies tcp
            WHERE tcp.tenant_id = cfg.tenant_id AND tcp.is_active = TRUE
        ) AS concurrency_policy_ready,
        COALESCE(prc.inbound_enabled, FALSE) AS platform_inbound_enabled,
        COALESCE(prc.inbound_recording_enabled, FALSE) AS platform_recording_enabled,
        COALESCE(prc.inbound_transfer_enabled, FALSE) AS platform_transfer_enabled,
        COALESCE(prc.inbound_settlement_enabled, FALSE) AS platform_settlement_enabled,
        a.id AS assignment_id,
        a.phone_number_id,
        a.sip_trunk_id,
        a.canonical_did,
        a.status AS assignment_status,
        a.version AS assignment_version,
        a.status_before_quarantine,
        a.quarantine_reason,
        a.last_error,
        pn.status AS phone_status,
        pn.verified_at,
        st.trunk_name AS sip_trunk_name,
        st.direction AS trunk_direction,
        st.is_active AS trunk_active,
        st.metadata AS trunk_metadata,
        st.live_registration_status AS trunk_live_registration_status,
        st.live_status_detail AS trunk_live_status_detail,
        st.live_status_checked_at AS trunk_live_status_checked_at,
        EXISTS (
            SELECT 1
            FROM inbound_did_assignments conflict
            WHERE conflict.canonical_did = a.canonical_did
              AND conflict.status = 'active'
              AND conflict.id <> a.id
        ) AS active_did_conflict,
        (
            SELECT MAX(call_row.created_at)
            FROM calls call_row
            WHERE call_row.assignment_id = a.id
              AND call_row.direction = 'inbound'
        ) AS last_call_at
    FROM inbound_campaign_configs cfg
    JOIN campaigns c
      ON c.id = cfg.campaign_id AND c.tenant_id = cfg.tenant_id
    JOIN LATERAL (
        SELECT candidate.*
        FROM inbound_did_assignments candidate
        WHERE candidate.config_id = cfg.id
        ORDER BY (candidate.status <> 'archived') DESC,
                 candidate.created_at DESC
        LIMIT 1
    ) a ON TRUE
    JOIN tenant_phone_numbers pn
      ON pn.id = a.phone_number_id AND pn.tenant_id = a.tenant_id
    JOIN tenant_sip_trunks st
      ON st.id = a.sip_trunk_id AND st.tenant_id = a.tenant_id
    JOIN tenants t ON t.id = cfg.tenant_id
    LEFT JOIN tenant_inbound_controls tic ON tic.tenant_id = cfg.tenant_id
    LEFT JOIN tenant_ai_configs ai ON ai.tenant_id = cfg.tenant_id
    LEFT JOIN platform_runtime_controls prc ON prc.id = 1
"""


class InboundCampaignService:
    def __init__(self, db_pool: Any):
        self._pool = db_pool

    # ------------------------------------------------------------------
    # Durable idempotency + audit
    # ------------------------------------------------------------------

    async def _claim_operation(
        self,
        conn: asyncpg.Connection,
        *,
        tenant_id: Optional[str],
        scope_key: str,
        operation: str,
        idempotency_key: str,
        actor_id: str,
        request_payload: Mapping[str, Any],
    ) -> Optional[dict[str, Any]]:
        if not 8 <= len(idempotency_key) <= 255:
            raise InboundCampaignError(
                "Idempotency-Key must be between 8 and 255 characters",
                code="invalid_idempotency_key",
                status_code=400,
            )
        request_hash = _stable_hash(request_payload)
        inserted = await conn.fetchrow(
            """
            INSERT INTO inbound_operation_idempotency (
                tenant_id, scope_key, operation, idempotency_key,
                request_hash, actor_id
            )
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (scope_key, operation, idempotency_key) DO UPDATE
            SET tenant_id = EXCLUDED.tenant_id,
                request_hash = EXCLUDED.request_hash,
                response_body = NULL,
                status_code = NULL,
                resource_type = NULL,
                resource_id = NULL,
                actor_id = EXCLUDED.actor_id,
                created_at = NOW(),
                expires_at = NOW() + INTERVAL '24 hours'
            WHERE inbound_operation_idempotency.expires_at <= NOW()
            RETURNING id
            """,
            tenant_id,
            scope_key,
            operation,
            idempotency_key,
            request_hash,
            actor_id,
        )
        if inserted:
            return None

        existing = await conn.fetchrow(
            """
            SELECT request_hash, response_body, status_code
            FROM inbound_operation_idempotency
            WHERE scope_key = $1 AND operation = $2 AND idempotency_key = $3
            """,
            scope_key,
            operation,
            idempotency_key,
        )
        if not existing:
            raise InboundConflictError("Idempotency claim disappeared", code="idempotency_race")
        if existing["request_hash"] != request_hash:
            raise InboundConflictError(
                "Idempotency-Key was reused with a different request",
                code="idempotency_mismatch",
            )
        if existing["response_body"] is None or existing["status_code"] is None:
            raise InboundConflictError(
                "An operation with this Idempotency-Key is still in progress",
                code="idempotency_in_progress",
            )
        response = existing["response_body"]
        if isinstance(response, str):
            response = json.loads(response)
        return dict(response)

    async def _store_operation(
        self,
        conn: asyncpg.Connection,
        *,
        scope_key: str,
        operation: str,
        idempotency_key: str,
        response: Mapping[str, Any],
        resource_type: str,
        resource_id: Optional[str],
        status_code: int = 200,
    ) -> None:
        await conn.execute(
            """
            UPDATE inbound_operation_idempotency
            SET response_body = $4::jsonb,
                status_code = $5,
                resource_type = $6,
                resource_id = $7
            WHERE scope_key = $1 AND operation = $2 AND idempotency_key = $3
            """,
            scope_key,
            operation,
            idempotency_key,
            json.dumps(_json_safe(dict(response))),
            status_code,
            resource_type,
            resource_id,
        )

    async def _audit(
        self,
        conn: asyncpg.Connection,
        *,
        tenant_id: Optional[str],
        event_type: str,
        actor_id: str,
        actor_role: str,
        resource_type: str,
        resource_id: Optional[str],
        reason: Optional[str] = None,
        before: Optional[Mapping[str, Any]] = None,
        after: Optional[Mapping[str, Any]] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        idempotency_key: Optional[str] = None,
    ) -> None:
        await conn.execute(
            """
            INSERT INTO inbound_audit_events (
                tenant_id, event_type, actor_id, actor_role, resource_type,
                resource_id, reason, before_state, after_state, metadata,
                idempotency_key
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9::jsonb,
                    $10::jsonb, $11)
            """,
            tenant_id,
            event_type,
            actor_id,
            actor_role,
            resource_type,
            resource_id,
            reason,
            json.dumps(_json_safe(dict(before))) if before is not None else None,
            json.dumps(_json_safe(dict(after))) if after is not None else None,
            json.dumps(_json_safe(dict(metadata or {}))),
            idempotency_key,
        )

    # ------------------------------------------------------------------
    # Loading / readiness / serialization
    # ------------------------------------------------------------------

    async def _load_bundle(
        self,
        conn: asyncpg.Connection,
        config_id: str,
        *,
        tenant_id: Optional[str] = None,
    ) -> dict[str, Any]:
        args: list[Any] = [config_id]
        where = " WHERE cfg.id = $1"
        if tenant_id is not None:
            args.append(tenant_id)
            where += " AND cfg.tenant_id = $2"
        row = await conn.fetchrow(_BUNDLE_SQL + where, *args)
        if not row:
            raise InboundNotFoundError()
        return dict(row)

    @staticmethod
    def _readiness(bundle: Mapping[str, Any]) -> dict[str, Any]:
        checks: list[ReadinessCheck] = []

        def add(key: str, label: str, passed: bool, detail: str) -> None:
            checks.append(ReadinessCheck(key, label, bool(passed), detail))

        add(
            "campaign_direction",
            "Inbound campaign direction",
            bundle.get("campaign_direction") == "inbound",
            (
                "Campaign is reserved for inbound traffic."
                if bundle.get("campaign_direction") == "inbound"
                else "Campaign is still marked outbound."
            ),
        )
        base_status = str(bundle.get("campaign_status") or "").strip().lower()
        base_campaign_active = is_active_inbound_campaign_status(base_status)
        # A draft/paused inbound config can atomically start a non-terminal
        # base campaign in set_lifecycle(). Treat that state as activation-
        # ready so the UI does not deadlock with a disabled Activate button.
        base_campaign_activatable = base_campaign_active or (
            bundle.get("config_status") in {"draft", "paused"}
            and base_status in {"draft", "paused", "stopped"}
        )
        add(
            "campaign_active",
            "Base campaign activation state",
            base_campaign_activatable,
            (
                (
                    "Base campaign is active for inbound traffic."
                    if base_campaign_active
                    else "Base campaign will start atomically with inbound activation."
                )
                if base_campaign_activatable
                else "Base campaign must be running before it can receive calls."
            ),
        )
        tenant_active = bundle.get("tenant_status") == "active" and bundle.get(
            "subscription_status"
        ) in {"active", "trialing"}
        add(
            "tenant_active",
            "Tenant and subscription active",
            tenant_active,
            (
                "Tenant is eligible for inbound service."
                if tenant_active
                else "Tenant or subscription is not active."
            ),
        )
        inbound_enabled = bool(bundle.get("tenant_inbound_enabled"))
        add(
            "tenant_inbound_enabled",
            "Tenant inbound control enabled",
            inbound_enabled,
            (
                "Tenant inbound control is enabled."
                if inbound_enabled
                else "Tenant inbound calling is paused."
            ),
        )
        platform_inbound_enabled = bool(bundle.get("platform_inbound_enabled"))
        add(
            "platform_inbound_enabled",
            "Platform inbound service enabled",
            platform_inbound_enabled,
            (
                "Platform inbound admission is enabled."
                if platform_inbound_enabled
                else "Platform inbound admission is paused."
            ),
        )
        platform_settlement_enabled = bool(bundle.get("platform_settlement_enabled"))
        add(
            "platform_settlement_enabled",
            "Platform inbound settlement enabled",
            platform_settlement_enabled,
            (
                "Inbound settlement is enabled."
                if platform_settlement_enabled
                else "Inbound settlement is paused; calls cannot be activated safely."
            ),
        )
        qualification_values, qualification_unsupported, qualification_invalid = (
            validate_qualification_overrides(_json_obj(bundle.get("qualification_config")))
        )
        has_prompt = (
            bool(qualification_values.get("system_prompt"))
            or bool(str(bundle.get("system_prompt") or "").strip())
            or bool(_json_obj(bundle.get("script_config")))
        )
        add(
            "campaign_prompt",
            "Prompt configured",
            has_prompt,
            "Prompt configuration is present." if has_prompt else "Add a prompt before activation.",
        )
        ai_ready = bool(bundle.get("tenant_ai_config_id")) and all(
            str(bundle.get(field) or "").strip()
            for field in (
                "llm_provider",
                "llm_model",
                "stt_provider",
                "stt_model",
                "tenant_tts_provider",
                "tenant_tts_model",
            )
        )
        add(
            "ai_providers_configured",
            "AI providers configured",
            ai_ready,
            (
                "Tenant LLM and speech providers are configured."
                if ai_ready
                else "Configure the tenant LLM and speech providers."
            ),
        )
        voice_ready = bool(
            str(
                qualification_values.get("voice_id")
                or bundle.get("voice_id")
                or bundle.get("tenant_tts_voice_id")
                or ""
            ).strip()
        ) and bool(
            str(bundle.get("tts_provider") or bundle.get("tenant_tts_provider") or "").strip()
        )
        add(
            "voice_configured",
            "Voice configured",
            voice_ready,
            (
                "A TTS provider and voice are selected."
                if voice_ready
                else "Select a working TTS provider and voice."
            ),
        )
        pipeline_mode = str(bundle.get("tenant_pipeline_mode") or "cascaded")
        pipeline_ready = pipeline_mode in {"cascaded", "realtime"}
        add(
            "pipeline_mode_valid",
            "AI pipeline mode valid",
            pipeline_ready,
            (
                f"The {pipeline_mode} voice pipeline is supported."
                if pipeline_ready
                else "Choose the cascaded or realtime voice pipeline."
            ),
        )
        add(
            "did_verified",
            "DID ownership verified",
            bundle.get("phone_status") == "verified",
            (
                "DID is verified."
                if bundle.get("phone_status") == "verified"
                else "DID is not verified."
            ),
        )
        trunk_runtime = evaluate_trunk_runtime(bundle)
        add(
            "trunk_ready",
            "Inbound SIP trunk runtime healthy",
            trunk_runtime.ready,
            trunk_runtime.detail,
        )
        allocated_minutes = int(bundle.get("minutes_allocated") or 0)
        quota_ready = True
        add(
            "billing_quota_configured",
            "Inbound quota policy resolved",
            quota_ready,
            (
                f"Tenant has {allocated_minutes} billable minutes allocated."
                if allocated_minutes > 0
                else "Tenant uses the unlimited-minute quota policy."
            ),
        )
        concurrency_ready = bool(bundle.get("concurrency_policy_ready"))
        add(
            "concurrency_policy_configured",
            "Concurrency policy configured",
            concurrency_ready,
            (
                "An active tenant concurrency policy is present."
                if concurrency_ready
                else "Create an active tenant concurrency policy."
            ),
        )
        assignment_ok = bundle.get("assignment_status") != "quarantined"
        add(
            "did_not_quarantined",
            "DID is not quarantined",
            assignment_ok,
            (
                "DID may be activated."
                if assignment_ok
                else "Platform quarantine must be cleared first."
            ),
        )
        no_conflict = not bool(bundle.get("active_did_conflict"))
        add(
            "did_unambiguous",
            "DID has one route",
            no_conflict,
            (
                "No competing active route exists."
                if no_conflict
                else "Another active assignment owns this DID."
            ),
        )
        try:
            ZoneInfo(str(bundle.get("timezone") or ""))
            timezone_ok = True
        except (ZoneInfoNotFoundError, ValueError):
            timezone_ok = False
        add(
            "timezone_valid",
            "Timezone valid",
            timezone_ok,
            "IANA timezone is valid." if timezone_ok else "Choose a valid IANA timezone.",
        )
        schedule_valid = evaluate_business_hours(
            str(bundle.get("timezone") or ""),
            _json_obj(bundle.get("business_hours")),
        ).valid
        add(
            "business_hours_valid",
            "Business-hours schedule valid",
            schedule_valid,
            (
                "Weekly schedule is deterministic."
                if schedule_valid
                else "Fix malformed days, HH:MM windows, holidays, or policy values."
            ),
        )
        after_hours_message = _json_obj(bundle.get("business_hours")).get("after_hours_message")
        after_hours_message_valid = (
            after_hours_message is None or isinstance(after_hours_message, str)
        ) and (
            bundle.get("after_hours_action") != "voicemail"
            or bool(str(after_hours_message or "").strip())
        )
        add(
            "after_hours_message_valid",
            "After-hours message valid",
            after_hours_message_valid,
            (
                (
                    "A pinned greeting is configured for conversational AI " "message intake."
                    if bundle.get("after_hours_action") == "voicemail"
                    else "The optional after-hours message is valid."
                )
                if after_hours_message_valid
                else (
                    "Conversational AI message intake requires a non-empty " "after-hours greeting."
                    if bundle.get("after_hours_action") == "voicemail"
                    else "After-hours message must be text."
                )
            ),
        )
        from app.domain.services.telephony.inbound_transfer import (
            inbound_transfer_destination_approved,
            inbound_transfer_scope_available,
        )

        transfer_policy = _json_obj(bundle.get("transfer_policy"))
        transfer_number = str(bundle.get("transfer_number") or "").strip()
        transfer_ok = bundle.get("after_hours_action") != "transfer" or (
            bool(transfer_number)
            and transfer_policy.get("enabled") is True
            and inbound_transfer_destination_approved(
                transfer_policy,
                transfer_number,
            )
        )
        add(
            "after_hours_complete",
            "After-hours action configured",
            transfer_ok,
            (
                "After-hours policy is complete."
                if transfer_ok
                else "Enable the transfer policy and provide an approved destination."
            ),
        )
        transfer_requested = _transfer_requested(bundle)
        transfer_runtime_available = inbound_transfer_scope_available(
            tenant_id=bundle.get("tenant_id"),
            config_id=bundle.get("config_id"),
        )
        after_hours_supported = bundle.get("after_hours_action") in {
            "hangup",
            "voicemail",
        } or (bundle.get("after_hours_action") == "transfer" and transfer_runtime_available)
        add(
            "after_hours_runtime_supported",
            "After-hours runtime supported",
            after_hours_supported,
            (
                (
                    "Conversational AI message intake is supported; it records the "
                    "ordinary call transcript and outcome, not a one-way voicemail artifact."
                    if bundle.get("after_hours_action") == "voicemail"
                    else "The selected after-hours action is supported by the pinned runtime contract."
                )
                if after_hours_supported
                else (
                    "Controlled inbound transfer is unavailable until the linked-leg "
                    "runtime passes live-carrier release validation."
                    if bundle.get("after_hours_action") == "transfer"
                    else "Choose hangup or voicemail."
                )
            ),
        )
        transfer_trunk_direction_ready = (
            not transfer_requested
            or str(bundle.get("trunk_direction") or "").strip().lower() == "both"
        )
        add(
            "transfer_trunk_bidirectional",
            "Transfer SIP trunk is bidirectional",
            transfer_trunk_direction_ready,
            (
                "The campaign does not request inbound transfer capability."
                if not transfer_requested
                else (
                    "The selected SIP trunk supports both inbound and outbound legs."
                    if transfer_trunk_direction_ready
                    else (
                        "Inbound transfer requires a trunk whose direction is both; "
                        "an inbound-only trunk cannot originate the transfer leg."
                    )
                )
            ),
        )
        transfer_runtime_supported = not transfer_requested or transfer_runtime_available
        add(
            "transfer_runtime_supported",
            "Controlled inbound transfer runtime available",
            transfer_runtime_supported,
            (
                "No inbound transfer capability is enabled for this campaign."
                if transfer_runtime_supported and not transfer_requested
                else (
                    "The controlled linked-leg runtime is available."
                    if transfer_runtime_supported
                    else (
                        "Inbound transfer stays unavailable until Talky owns both call legs, "
                        "enforces the pinned deadline, and settles linked-leg usage."
                    )
                )
            ),
        )
        platform_transfer_ready = not transfer_requested or bool(
            bundle.get("platform_transfer_enabled")
        )
        add(
            "platform_transfer_enabled",
            "Platform inbound transfer control enabled",
            platform_transfer_ready,
            (
                "The campaign does not request inbound transfer capability."
                if not transfer_requested
                else (
                    "The platform transfer kill switch is enabled."
                    if platform_transfer_ready
                    else "A platform administrator must enable the transfer kill switch."
                )
            ),
        )
        max_duration = transfer_policy.get("max_call_duration_seconds", 1_800)
        max_duration_valid = (
            not isinstance(max_duration, bool)
            and isinstance(max_duration, int)
            and 60 <= max_duration <= 14_400
        )
        add(
            "max_call_duration_valid",
            "Maximum inbound call duration valid",
            max_duration_valid,
            (
                f"Calls are capped and reserved for at most {max_duration} seconds."
                if max_duration_valid
                else "Maximum call duration must be a whole number from 60 to 14400 seconds."
            ),
        )
        consent_ok = not bool(bundle.get("recording_enabled")) or bool(
            str(bundle.get("consent_message") or "").strip()
        )
        add(
            "recording_consent",
            "Recording consent configured",
            consent_ok,
            "Recording policy is complete." if consent_ok else "Consent message is required.",
        )
        platform_recording_ok = not bool(bundle.get("recording_enabled")) or bool(
            bundle.get("platform_recording_enabled")
        )
        add(
            "platform_recording_enabled",
            "Platform recording control enabled",
            platform_recording_ok,
            (
                "Recording is permitted by the platform control."
                if platform_recording_ok
                else "Enable the platform recording control or disable campaign recording."
            ),
        )
        qualification_supported = not qualification_unsupported and not qualification_invalid
        qualification_detail = (
            "Pinned purpose, persona, system-prompt, voice, and opening-silence "
            "overrides are supported."
        )
        if qualification_unsupported:
            qualification_detail = "Unsupported inbound controls: " + ", ".join(
                qualification_unsupported
            )
        elif qualification_invalid:
            qualification_detail = "Invalid inbound override values: " + ", ".join(
                qualification_invalid
            )
        add(
            "qualification_runtime_supported",
            "Inbound-specific AI overrides supported",
            qualification_supported,
            qualification_detail,
        )
        rendered = [asdict(check) for check in checks]
        remediations = {
            "campaign_direction": "Use an unused draft campaign and convert it to inbound.",
            "campaign_active": "Activate the base campaign for inbound service.",
            "tenant_active": "Restore the tenant and resolve subscription status.",
            "tenant_inbound_enabled": "Enable the tenant inbound control.",
            "platform_inbound_enabled": "Ask a platform administrator to enable inbound admission after runtime validation.",
            "platform_settlement_enabled": "Ask a platform administrator to enable inbound settlement.",
            "campaign_prompt": "Add a validated system prompt or script configuration.",
            "ai_providers_configured": "Save valid tenant LLM and speech provider settings.",
            "voice_configured": "Select a valid TTS provider and voice.",
            "pipeline_mode_valid": "Choose cascaded or realtime in the tenant AI configuration.",
            "did_verified": "Complete carrier, SMS, LOA, or platform-admin verification first.",
            "trunk_ready": (
                "Enable an inbound-capable trunk, then wait for fresh Asterisk "
                "endpoint or registration evidence. Check the trunk live-status detail."
            ),
            "billing_quota_configured": "Provision an explicit monthly quota or unlimited policy.",
            "concurrency_policy_configured": "Create and activate a tenant concurrency policy.",
            "did_not_quarantined": "Ask a platform administrator to review and unquarantine the DID.",
            "did_unambiguous": "Archive the competing route or complete four-eye reassignment.",
            "timezone_valid": "Choose a timezone from the IANA timezone database.",
            "business_hours_valid": "Save a valid weekly schedule using unique days and HH:MM windows.",
            "after_hours_message_valid": (
                "For conversational AI message intake, save a non-empty text "
                "after_hours_message; otherwise save text or remove the field."
            ),
            "after_hours_complete": (
                "Enable the transfer policy and add the selected approved destination."
            ),
            "after_hours_runtime_supported": "Choose hangup or voicemail until controlled transfer passes release validation.",
            "transfer_runtime_supported": (
                "Disable inbound transfer until the controlled linked-leg runtime passes release validation."
            ),
            "platform_transfer_enabled": (
                "Ask a platform administrator to enable transfer only for the approved proof window."
            ),
            "transfer_trunk_bidirectional": (
                "Select an active trunk with direction set to both, or disable inbound transfer."
            ),
            "max_call_duration_valid": "Set max_call_duration_seconds to a whole number from 60 to 14400; omit it to use 1800.",
            "recording_consent": "Provide an approved recording consent message or disable recording.",
            "platform_recording_enabled": "Enable the platform recording control or disable campaign recording.",
            "qualification_runtime_supported": (
                "Use the supported purpose, persona, system_prompt, voice_id, "
                "and silence_timeout_seconds fields; configure knowledge and "
                "executable tools on the selected base campaign."
            ),
        }
        blockers = [
            {
                "code": check["key"],
                "message": check["detail"],
                "remediation": remediations[check["key"]],
            }
            for check in rendered
            if not check["passed"]
        ]
        return {
            "ready": not blockers,
            "checks": rendered,
            "blockers": blockers,
        }

    def _serialize_bundle(self, bundle: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "id": str(bundle["config_id"]),
            "tenant_id": str(bundle["tenant_id"]),
            "name": bundle["name"],
            "status": bundle["config_status"],
            "version": int(bundle["config_version"]),
            "config_version": int(bundle["config_version"]),
            "config_checksum": bundle["config_checksum"],
            "did_number": bundle["canonical_did"],
            "assignment_id": str(bundle["assignment_id"]),
            "assignment_status": bundle["assignment_status"],
            "assignment_version": int(bundle["assignment_version"]),
            "campaign_id": str(bundle["campaign_id"]),
            "campaign_name": bundle.get("campaign_name"),
            "sip_trunk_id": str(bundle["sip_trunk_id"]),
            "sip_trunk_name": bundle.get("sip_trunk_name"),
            "timezone": bundle["timezone"],
            "after_hours_action": bundle["after_hours_action"],
            "transfer_number": bundle.get("transfer_number"),
            "recording_enabled": bool(bundle["recording_enabled"]),
            "consent_message": bundle.get("consent_message"),
            "opening_mode": bundle["opening_mode"],
            "greeting": bundle.get("greeting"),
            "business_hours": _json_obj(bundle.get("business_hours")),
            "recording_policy": _json_obj(bundle.get("recording_policy")),
            "transfer_policy": _json_obj(bundle.get("transfer_policy")),
            "qualification_config": _json_obj(bundle.get("qualification_config")),
            "readiness": self._readiness(bundle),
            "last_call_at": bundle.get("last_call_at"),
            "last_error": bundle.get("last_error"),
            "active_at": bundle.get("active_at"),
            "created_at": bundle["created_at"],
            "updated_at": bundle["updated_at"],
        }

    async def list_campaigns(
        self,
        *,
        tenant_id: str,
        include_archived: bool = False,
    ) -> list[dict[str, Any]]:
        tenant_id = _uuid(tenant_id, "tenant_id")
        archived_clause = "" if include_archived else " AND cfg.status <> 'archived'"
        async with acquire_with_tenant(self._pool, None) as conn:
            rows = await conn.fetch(
                _BUNDLE_SQL
                + " WHERE cfg.tenant_id = $1"
                + archived_clause
                + " ORDER BY cfg.updated_at DESC",
                tenant_id,
            )
        return [self._serialize_bundle(dict(row)) for row in rows]

    async def get_campaign(self, *, tenant_id: str, config_id: str) -> dict[str, Any]:
        tenant_id = _uuid(tenant_id, "tenant_id")
        config_id = _uuid(config_id, "config_id")
        async with acquire_with_tenant(self._pool, None) as conn:
            return self._serialize_bundle(
                await self._load_bundle(conn, config_id, tenant_id=tenant_id)
            )

    async def readiness(self, *, tenant_id: str, config_id: str) -> dict[str, Any]:
        campaign = await self.get_campaign(tenant_id=tenant_id, config_id=config_id)
        return campaign["readiness"]

    # ------------------------------------------------------------------
    # Tenant lifecycle
    # ------------------------------------------------------------------

    async def _verified_phone(self, conn, *, tenant_id: str, did: str) -> Mapping[str, Any]:
        row = await conn.fetchrow(
            """
            SELECT id, status FROM tenant_phone_numbers
            WHERE tenant_id = $1 AND e164 = $2
            """,
            tenant_id,
            did,
        )
        if not row or row["status"] != "verified":
            raise InboundConflictError(
                "DID must be verified for this tenant before assignment",
                code="did_not_verified",
            )
        return row

    async def _active_trunk(self, conn, *, tenant_id: str, trunk_id: str) -> Mapping[str, Any]:
        row = await conn.fetchrow(
            """
            SELECT id, trunk_name, direction, is_active
            FROM tenant_sip_trunks
            WHERE tenant_id = $1 AND id = $2
            """,
            tenant_id,
            trunk_id,
        )
        if not row or not row["is_active"] or row["direction"] not in {"inbound", "both"}:
            raise InboundConflictError(
                "SIP trunk must be active and inbound-capable", code="trunk_not_ready"
            )
        return row

    async def _lock_assignment_for_config(
        self,
        conn,
        *,
        config_id: str,
        tenant_id: str,
    ) -> Mapping[str, Any]:
        """Lock the config's current (or terminal archived) assignment.

        Callers must lock ``inbound_campaign_configs`` first.  Every tenant
        edit/lifecycle path and the platform quarantine path follows that same
        order, preventing the config/assignment deadlock and stale-state races
        that otherwise let a quarantine be silently overwritten.
        """
        row = await conn.fetchrow(
            """
            SELECT *
            FROM inbound_did_assignments
            WHERE config_id=$1 AND tenant_id=$2
            ORDER BY (status <> 'archived') DESC, created_at DESC
            LIMIT 1
            FOR UPDATE
            """,
            config_id,
            tenant_id,
        )
        if not row:
            raise InboundNotFoundError("Inbound DID assignment not found")
        return row

    async def _assert_did_free(
        self,
        conn,
        *,
        did: str,
        exclude_assignment_id: Optional[str] = None,
    ) -> None:
        row = await conn.fetchrow(
            """
            SELECT id, tenant_id, status
            FROM inbound_did_assignments
            WHERE canonical_did = $1
              AND status <> 'archived'
              AND ($2::uuid IS NULL OR id <> $2::uuid)
            ORDER BY created_at
            LIMIT 1
            """,
            did,
            exclude_assignment_id,
        )
        if row:
            raise InboundConflictError(
                "DID already has a live assignment; use the reassignment workflow",
                code="did_assignment_conflict",
            )

    async def create_campaign(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        actor_role: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        tenant_id = _uuid(tenant_id, "tenant_id")
        actor_id = _uuid(actor_id, "actor_id")
        campaign_id = _uuid(str(payload.get("campaign_id")), "campaign_id")
        trunk_id = _uuid(str(payload.get("sip_trunk_id")), "sip_trunk_id")
        did = normalize_did(str(payload.get("did_number") or ""))
        if not did:
            raise InboundCampaignError("Invalid DID", code="invalid_did", status_code=422)
        timezone = _timezone(str(payload.get("timezone") or "UTC"))
        normalized = dict(payload)
        normalized.update(
            {
                "campaign_id": campaign_id,
                "sip_trunk_id": trunk_id,
                "did_number": did,
                "timezone": timezone,
                "name": str(payload.get("name") or "").strip(),
            }
        )
        if not normalized["name"]:
            raise InboundCampaignError("name is required", code="invalid_name", status_code=422)
        if (
            normalized.get("after_hours_action") == "transfer"
            and not str(normalized.get("transfer_number") or "").strip()
        ):
            raise InboundCampaignError(
                "transfer_number is required", code="incomplete_after_hours", status_code=422
            )
        if (
            normalized.get("recording_enabled")
            and not str(normalized.get("consent_message") or "").strip()
        ):
            raise InboundCampaignError(
                "consent_message is required", code="missing_recording_consent", status_code=422
            )
        operation = "inbound_campaign.create"
        scope = f"tenant:{tenant_id}"
        async with acquire_with_tenant(self._pool, None) as conn:
            replay = await self._claim_operation(
                conn,
                tenant_id=tenant_id,
                scope_key=scope,
                operation=operation,
                idempotency_key=idempotency_key,
                actor_id=actor_id,
                request_payload=normalized,
            )
            if replay is not None:
                replay["idempotent_replay"] = True
                return replay
            await _require_transfer_configuration(
                conn,
                normalized,
                tenant_id=tenant_id,
                config_id=None,
            )

            campaign = await conn.fetchrow(
                "SELECT * FROM campaigns WHERE id = $1 AND tenant_id = $2 FOR UPDATE",
                campaign_id,
                tenant_id,
            )
            if not campaign:
                raise InboundNotFoundError("Base campaign not found")
            if campaign["direction"] == "outbound":
                if campaign["status"] != "draft":
                    raise InboundConflictError(
                        "Only a draft outbound campaign can be converted to inbound",
                        code="campaign_direction_conflict",
                    )
                used = await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM calls WHERE campaign_id = $1)", campaign_id
                )
                queued = await conn.fetchval(
                    """
                    SELECT EXISTS(
                        SELECT 1 FROM dialer_jobs
                        WHERE campaign_id = $1
                          AND status IN ('pending', 'processing', 'retry_scheduled')
                    )
                    """,
                    campaign_id,
                )
                if used or queued:
                    raise InboundConflictError(
                        "Campaign already has outbound activity and cannot change direction",
                        code="campaign_direction_conflict",
                    )
                await conn.execute(
                    "UPDATE campaigns SET direction='inbound', updated_at=NOW() WHERE id=$1",
                    campaign_id,
                )

            existing_config = await conn.fetchval(
                "SELECT id FROM inbound_campaign_configs WHERE campaign_id = $1", campaign_id
            )
            if existing_config:
                raise InboundConflictError(
                    "Campaign already has an inbound configuration",
                    code="config_already_exists",
                )
            phone = await self._verified_phone(conn, tenant_id=tenant_id, did=did)
            await self._active_trunk(conn, tenant_id=tenant_id, trunk_id=trunk_id)
            await self._assert_did_free(conn, did=did)

            checksum = _config_checksum(normalized)
            config = await conn.fetchrow(
                """
                INSERT INTO inbound_campaign_configs (
                    tenant_id, campaign_id, name, opening_mode, greeting,
                    timezone, business_hours, after_hours_action, transfer_number,
                    recording_enabled, consent_message, recording_policy,
                    transfer_policy, qualification_config, config_checksum,
                    created_by, updated_by
                )
                VALUES (
                    $1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9, $10, $11,
                    $12::jsonb, $13::jsonb, $14::jsonb, $15, $16, $16
                )
                RETURNING id
                """,
                tenant_id,
                campaign_id,
                normalized["name"],
                normalized.get("opening_mode", "caller_first"),
                normalized.get("greeting"),
                timezone,
                json.dumps(normalized.get("business_hours") or {}),
                normalized.get("after_hours_action", "hangup"),
                normalized.get("transfer_number"),
                bool(normalized.get("recording_enabled", False)),
                normalized.get("consent_message"),
                json.dumps(normalized.get("recording_policy") or {}),
                json.dumps(normalized.get("transfer_policy") or {}),
                json.dumps(normalized.get("qualification_config") or {}),
                checksum,
                actor_id,
            )
            try:
                assignment = await conn.fetchrow(
                    """
                    INSERT INTO inbound_did_assignments (
                        tenant_id, phone_number_id, campaign_id, config_id,
                        sip_trunk_id, canonical_did, status, created_by, updated_by
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, 'paused', $7, $7)
                    RETURNING id
                    """,
                    tenant_id,
                    phone["id"],
                    campaign_id,
                    config["id"],
                    trunk_id,
                    did,
                    actor_id,
                )
            except asyncpg.UniqueViolationError as exc:
                raise InboundConflictError(
                    "DID already has a live assignment; use the reassignment workflow",
                    code="did_assignment_conflict",
                ) from exc
            await conn.execute(
                """
                INSERT INTO tenant_inbound_controls (tenant_id, inbound_enabled, updated_by)
                VALUES ($1, FALSE, $2)
                ON CONFLICT (tenant_id) DO NOTHING
                """,
                tenant_id,
                actor_id,
            )
            result = self._serialize_bundle(
                await self._load_bundle(conn, str(config["id"]), tenant_id=tenant_id)
            )
            await self._audit(
                conn,
                tenant_id=tenant_id,
                event_type="inbound_campaign_created",
                actor_id=actor_id,
                actor_role=actor_role,
                resource_type="inbound_campaign",
                resource_id=str(config["id"]),
                after=result,
                metadata={"assignment_id": str(assignment["id"]), "did_ref": redact_did(did)},
                idempotency_key=idempotency_key,
            )
            await self._store_operation(
                conn,
                scope_key=scope,
                operation=operation,
                idempotency_key=idempotency_key,
                response=result,
                resource_type="inbound_campaign",
                resource_id=str(config["id"]),
                status_code=201,
            )
            return result

    async def update_campaign(
        self,
        *,
        tenant_id: str,
        config_id: str,
        actor_id: str,
        actor_role: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
        assignment_workflow: bool = False,
    ) -> dict[str, Any]:
        tenant_id = _uuid(tenant_id, "tenant_id")
        config_id = _uuid(config_id, "config_id")
        actor_id = _uuid(actor_id, "actor_id")
        expected = int(payload.get("expected_version") or 0)
        if expected < 1:
            raise InboundCampaignError(
                "expected_version is required", code="expected_version_required", status_code=422
            )
        operation_kind = "assign" if assignment_workflow else "update"
        operation = f"inbound_campaign.{operation_kind}:{config_id}"
        scope = f"tenant:{tenant_id}"
        request_payload = dict(payload)
        async with acquire_with_tenant(self._pool, None) as conn:
            replay = await self._claim_operation(
                conn,
                tenant_id=tenant_id,
                scope_key=scope,
                operation=operation,
                idempotency_key=idempotency_key,
                actor_id=actor_id,
                request_payload=request_payload,
            )
            if replay is not None:
                replay["idempotent_replay"] = True
                return replay
            locked = await conn.fetchrow(
                """
                SELECT id, version, status FROM inbound_campaign_configs
                WHERE id=$1 AND tenant_id=$2 FOR UPDATE
                """,
                config_id,
                tenant_id,
            )
            if not locked:
                raise InboundNotFoundError()
            if int(locked["version"]) != expected:
                raise InboundConflictError(
                    f"Version conflict: expected {expected}, current {locked['version']}"
                )
            current_status = str(locked["status"] or "").strip().lower()
            if current_status == "active":
                raise InboundConflictError(
                    "Pause the inbound campaign before editing it",
                    code="pause_before_edit",
                )
            if current_status == "archived":
                raise InboundConflictError(
                    "Archived inbound campaigns are immutable",
                    code="campaign_archived",
                )
            assignment = await self._lock_assignment_for_config(
                conn,
                config_id=config_id,
                tenant_id=tenant_id,
            )
            assignment_status = str(assignment["status"] or "").strip().lower()
            if assignment_status == "quarantined":
                raise InboundConflictError(
                    "Platform quarantine must be cleared before editing this campaign",
                    code="assignment_quarantined",
                )
            if assignment_status != "paused":
                raise InboundConflictError(
                    "DID assignment must be paused before editing this campaign",
                    code="assignment_state_conflict",
                )
            before = self._serialize_bundle(
                await self._load_bundle(conn, config_id, tenant_id=tenant_id)
            )
            requested_did = normalize_did(str(payload.get("did_number") or before["did_number"]))
            if not requested_did:
                raise InboundCampaignError("Invalid DID", code="invalid_did", status_code=422)
            requested_trunk_id = _uuid(
                str(payload.get("sip_trunk_id") or before["sip_trunk_id"]),
                "sip_trunk_id",
            )
            assignment_changed = (
                requested_did != before["did_number"]
                or requested_trunk_id != before["sip_trunk_id"]
            )
            if assignment_changed and not assignment_workflow:
                raise InboundConflictError(
                    "DID and trunk changes must use the explicit assignment endpoint",
                    code="assignment_workflow_required",
                )
            if (
                payload.get("campaign_id")
                and _uuid(str(payload["campaign_id"]), "campaign_id") != before["campaign_id"]
            ):
                raise InboundConflictError(
                    "Changing the base campaign is not supported", code="campaign_change_forbidden"
                )
            final: dict[str, Any] = {
                "name": before["name"],
                "campaign_id": before["campaign_id"],
                "opening_mode": before["opening_mode"],
                "greeting": before["greeting"],
                "timezone": before["timezone"],
                "business_hours": before["business_hours"],
                "after_hours_action": before["after_hours_action"],
                "transfer_number": before["transfer_number"],
                "recording_enabled": before["recording_enabled"],
                "consent_message": before["consent_message"],
                "recording_policy": before["recording_policy"],
                "transfer_policy": before["transfer_policy"],
                "qualification_config": before["qualification_config"],
            }
            for key in final:
                if key in payload:
                    final[key] = payload[key]
            final["name"] = str(final["name"] or "").strip()
            final["timezone"] = _timezone(str(final["timezone"]))
            if not final["name"]:
                raise InboundCampaignError("name is required", code="invalid_name", status_code=422)
            if (
                final["after_hours_action"] == "transfer"
                and not str(final.get("transfer_number") or "").strip()
            ):
                raise InboundCampaignError(
                    "transfer_number is required", code="incomplete_after_hours", status_code=422
                )
            if final["recording_enabled"] and not str(final.get("consent_message") or "").strip():
                raise InboundCampaignError(
                    "consent_message is required",
                    code="missing_recording_consent",
                    status_code=422,
                )
            await _require_transfer_configuration(
                conn,
                final,
                tenant_id=tenant_id,
                config_id=config_id,
            )

            did = requested_did
            trunk_id = requested_trunk_id
            phone = await self._verified_phone(conn, tenant_id=tenant_id, did=did)
            await self._active_trunk(conn, tenant_id=tenant_id, trunk_id=trunk_id)
            await self._assert_did_free(
                conn, did=did, exclude_assignment_id=before["assignment_id"]
            )
            checksum = _config_checksum(final)
            updated_config = await conn.fetchrow(
                """
                UPDATE inbound_campaign_configs
                SET name=$3, opening_mode=$4, greeting=$5, timezone=$6,
                    business_hours=$7::jsonb, after_hours_action=$8,
                    transfer_number=$9, recording_enabled=$10,
                    consent_message=$11, recording_policy=$12::jsonb,
                    transfer_policy=$13::jsonb, qualification_config=$14::jsonb,
                    config_checksum=$15, version=version+1,
                    updated_by=$16, updated_at=NOW()
                WHERE id=$1 AND tenant_id=$2 AND version=$17
                RETURNING version
                """,
                config_id,
                tenant_id,
                final["name"],
                final["opening_mode"],
                final["greeting"],
                final["timezone"],
                json.dumps(final["business_hours"] or {}),
                final["after_hours_action"],
                final["transfer_number"],
                bool(final["recording_enabled"]),
                final["consent_message"],
                json.dumps(final["recording_policy"] or {}),
                json.dumps(final["transfer_policy"] or {}),
                json.dumps(final["qualification_config"] or {}),
                checksum,
                actor_id,
                expected,
            )
            if not updated_config:
                raise InboundConflictError(
                    "Inbound campaign changed during update",
                    code="version_conflict",
                )
            if did != before["did_number"] or trunk_id != before["sip_trunk_id"]:
                try:
                    updated_assignment = await conn.fetchrow(
                        """
                        UPDATE inbound_did_assignments
                        SET phone_number_id=$3, canonical_did=$4, sip_trunk_id=$5,
                            version=version+1, updated_by=$6, updated_at=NOW()
                        WHERE id=$1 AND tenant_id=$2 AND version=$7
                          AND status='paused'
                        RETURNING id, version
                        """,
                        before["assignment_id"],
                        tenant_id,
                        phone["id"],
                        did,
                        trunk_id,
                        actor_id,
                        int(assignment["version"]),
                    )
                except asyncpg.UniqueViolationError as exc:
                    raise InboundConflictError(
                        "DID already has a live assignment; use the reassignment workflow",
                        code="did_assignment_conflict",
                    ) from exc
                if not updated_assignment:
                    raise InboundConflictError(
                        "DID assignment changed during update",
                        code="version_conflict",
                    )
            result = self._serialize_bundle(
                await self._load_bundle(conn, config_id, tenant_id=tenant_id)
            )
            await self._audit(
                conn,
                tenant_id=tenant_id,
                event_type=(
                    "inbound_campaign_assignment_updated"
                    if assignment_workflow and assignment_changed
                    else "inbound_campaign_updated"
                ),
                actor_id=actor_id,
                actor_role=actor_role,
                resource_type="inbound_campaign",
                resource_id=config_id,
                before=before,
                after=result,
                reason=str(payload.get("reason") or "").strip() or None,
                metadata={"did_ref": redact_did(did)},
                idempotency_key=idempotency_key,
            )
            await self._store_operation(
                conn,
                scope_key=scope,
                operation=operation,
                idempotency_key=idempotency_key,
                response=result,
                resource_type="inbound_campaign",
                resource_id=config_id,
            )
            return result

    async def set_lifecycle(
        self,
        *,
        tenant_id: str,
        config_id: str,
        target_status: str,
        actor_id: str,
        actor_role: str,
        idempotency_key: str,
        expected_version: int,
        reason: Optional[str] = None,
    ) -> dict[str, Any]:
        if target_status not in {"active", "paused", "archived"}:
            raise InboundCampaignError("Invalid lifecycle target", code="invalid_status")
        tenant_id = _uuid(tenant_id, "tenant_id")
        config_id = _uuid(config_id, "config_id")
        actor_id = _uuid(actor_id, "actor_id")
        operation = f"inbound_campaign.{target_status}:{config_id}"
        scope = f"tenant:{tenant_id}"
        expected_version = int(expected_version)
        if expected_version < 1:
            raise InboundCampaignError(
                "expected_version is required", code="expected_version_required", status_code=422
            )
        request_payload = {"expected_version": expected_version, "reason": reason}
        async with acquire_with_tenant(self._pool, None) as conn:
            replay = await self._claim_operation(
                conn,
                tenant_id=tenant_id,
                scope_key=scope,
                operation=operation,
                idempotency_key=idempotency_key,
                actor_id=actor_id,
                request_payload=request_payload,
            )
            if replay is not None:
                replay["idempotent_replay"] = True
                return replay
            locked = await conn.fetchrow(
                "SELECT version, status FROM inbound_campaign_configs WHERE id=$1 AND tenant_id=$2 FOR UPDATE",
                config_id,
                tenant_id,
            )
            if not locked:
                raise InboundNotFoundError()
            current_version = int(locked["version"])
            if current_version != expected_version:
                raise InboundConflictError(
                    f"Version conflict: expected {expected_version}, current {current_version}"
                )
            current_status = str(locked["status"] or "").strip().lower()
            if target_status == "archived" and current_status not in {"draft", "paused"}:
                raise InboundConflictError(
                    "Pause an active inbound campaign before archiving it",
                    code="pause_before_archive",
                )
            if current_status == "archived":
                raise InboundConflictError(
                    "Archived inbound campaigns are immutable",
                    code="campaign_archived",
                )
            assignment = await self._lock_assignment_for_config(
                conn,
                config_id=config_id,
                tenant_id=tenant_id,
            )
            assignment_status = str(assignment["status"] or "").strip().lower()
            if assignment_status == "quarantined":
                raise InboundConflictError(
                    "Platform quarantine must be cleared before changing lifecycle",
                    code="assignment_quarantined",
                )
            if assignment_status == "archived":
                raise InboundConflictError(
                    "Archived DID assignments are immutable",
                    code="assignment_archived",
                )
            before_bundle = await self._load_bundle(conn, config_id, tenant_id=tenant_id)
            before = self._serialize_bundle(before_bundle)
            assignment_id = before["assignment_id"]
            if target_status == "active":
                base_campaign = await conn.fetchrow(
                    """
                    SELECT id, direction, status FROM campaigns
                    WHERE id=$1 AND tenant_id=$2 FOR UPDATE
                    """,
                    before["campaign_id"],
                    tenant_id,
                )
                if not base_campaign or base_campaign["direction"] != "inbound":
                    raise InboundConflictError(
                        "Base campaign is not an inbound campaign",
                        code="campaign_not_inbound",
                    )
                if base_campaign["status"] in {"completed", "cancelled"}:
                    raise InboundConflictError(
                        "A completed or cancelled campaign cannot be activated",
                        code="campaign_terminal",
                    )
                if not is_active_inbound_campaign_status(base_campaign["status"]):
                    await conn.execute(
                        "UPDATE campaigns SET status='running', updated_at=NOW() "
                        "WHERE id=$1 AND tenant_id=$2",
                        before["campaign_id"],
                        tenant_id,
                    )
                # Re-read under the same transaction so readiness proves the
                # exact tenant, active campaign, verified DID, active trunk,
                # config and non-ambiguous assignment being committed.
                before_bundle = await self._load_bundle(conn, config_id, tenant_id=tenant_id)
                readiness = self._readiness(before_bundle)
                if not readiness["ready"]:
                    raise InboundReadinessError(readiness)
                try:
                    assignment_updated = await conn.fetchrow(
                        """
                        UPDATE inbound_did_assignments
                        SET status='active', status_before_quarantine=NULL,
                            quarantine_reason=NULL, version=version+1,
                            updated_by=$3, updated_at=NOW()
                        WHERE id=$1 AND tenant_id=$2 AND version=$4
                          AND status IN ('paused','active')
                        RETURNING id, version, status
                        """,
                        assignment_id,
                        tenant_id,
                        actor_id,
                        int(assignment["version"]),
                    )
                except asyncpg.UniqueViolationError as exc:
                    raise InboundConflictError(
                        "Another active assignment owns this DID",
                        code="did_assignment_conflict",
                    ) from exc
                if not assignment_updated:
                    raise InboundConflictError(
                        "DID assignment changed during activation",
                        code="version_conflict",
                    )
                active_at_sql = "COALESCE(active_at, NOW())"
            elif target_status == "paused":
                assignment_updated = await conn.fetchrow(
                    """
                    UPDATE inbound_did_assignments
                    SET status='paused', version=version+1, updated_by=$3, updated_at=NOW()
                    WHERE id=$1 AND tenant_id=$2 AND version=$4
                      AND status IN ('active','paused')
                    RETURNING id, version, status
                    """,
                    assignment_id,
                    tenant_id,
                    actor_id,
                    int(assignment["version"]),
                )
                if not assignment_updated:
                    raise InboundConflictError(
                        "DID assignment changed during pause",
                        code="version_conflict",
                    )
                active_at_sql = "active_at"
            else:
                assignment_updated = await conn.fetchrow(
                    """
                    UPDATE inbound_did_assignments
                    SET status='archived', valid_to=COALESCE(valid_to, NOW()),
                        version=version+1, updated_by=$3, updated_at=NOW()
                    WHERE id=$1 AND tenant_id=$2 AND version=$4
                      AND status IN ('active','paused')
                    RETURNING id, version, status
                    """,
                    assignment_id,
                    tenant_id,
                    actor_id,
                    int(assignment["version"]),
                )
                if not assignment_updated:
                    raise InboundConflictError(
                        "DID assignment changed during archive",
                        code="version_conflict",
                    )
                active_at_sql = "active_at"
            config_updated = await conn.fetchrow(
                f"""
                UPDATE inbound_campaign_configs
                SET status=$3, version=version+1, active_at={active_at_sql},
                    updated_by=$4, updated_at=NOW()
                WHERE id=$1 AND tenant_id=$2 AND version=$5
                RETURNING version, status, updated_at
                """,
                config_id,
                tenant_id,
                target_status,
                actor_id,
                current_version,
            )
            if not config_updated:
                raise InboundConflictError(
                    "Inbound campaign changed during lifecycle update",
                    code="version_conflict",
                )
            if target_status in {"paused", "archived"}:
                await conn.execute(
                    """
                    UPDATE campaigns
                    SET status=$3, updated_at=NOW()
                    WHERE id=$1 AND tenant_id=$2 AND direction='inbound'
                    """,
                    before["campaign_id"],
                    tenant_id,
                    "paused" if target_status == "paused" else "cancelled",
                )
            # The bundle loader includes terminal assignments, so every
            # lifecycle response is the actual committed row state (including
            # assignment version and updated timestamps), never a synthesized
            # pre-update copy.
            result = self._serialize_bundle(
                await self._load_bundle(conn, config_id, tenant_id=tenant_id)
            )
            await self._audit(
                conn,
                tenant_id=tenant_id,
                event_type=f"inbound_campaign_{target_status}",
                actor_id=actor_id,
                actor_role=actor_role,
                resource_type="inbound_campaign",
                resource_id=config_id,
                reason=reason,
                before=before,
                after=result,
                idempotency_key=idempotency_key,
            )
            await self._store_operation(
                conn,
                scope_key=scope,
                operation=operation,
                idempotency_key=idempotency_key,
                response=result,
                resource_type="inbound_campaign",
                resource_id=config_id,
            )
            return result

    async def did_availability(self, *, tenant_id: str, did_number: str) -> dict[str, Any]:
        tenant_id = _uuid(tenant_id, "tenant_id")
        did = normalize_did(did_number)
        if not did:
            raise InboundCampaignError("Invalid DID", code="invalid_did", status_code=422)
        async with acquire_with_tenant(self._pool, None) as conn:
            # Do not expose the platform-wide assignment inventory as an
            # existence oracle.  A tenant may only ask about a DID whose
            # ownership it has already proved.
            await self._verified_phone(conn, tenant_id=tenant_id, did=did)
            row = await conn.fetchrow(
                """
                SELECT id, tenant_id, status
                FROM inbound_did_assignments
                WHERE canonical_did=$1 AND status <> 'archived'
                ORDER BY CASE WHEN status='active' THEN 0 ELSE 1 END, created_at
                LIMIT 1
                """,
                did,
            )
        if not row:
            return {
                "did_number": did,
                "available": True,
                "reason": "available",
                "owned_by_current_tenant": False,
                "conflicting_assignment_id": None,
            }
        owned = str(row["tenant_id"]) == tenant_id
        return {
            "did_number": did,
            "available": False,
            "reason": "already_assigned_to_tenant" if owned else "reassignment_required",
            "owned_by_current_tenant": owned,
            "conflicting_assignment_id": str(row["id"]) if owned else None,
        }

    async def get_tenant_controls(self, *, tenant_id: str) -> dict[str, Any]:
        tenant_id = _uuid(tenant_id, "tenant_id")
        async with acquire_with_tenant(self._pool, None) as conn:
            row = await conn.fetchrow(
                """
                SELECT inbound_enabled, version, reason, updated_at
                FROM tenant_inbound_controls WHERE tenant_id=$1
                """,
                tenant_id,
            )
        if not row:
            return {"inbound_enabled": False, "version": 1, "reason": None, "updated_at": None}
        return {
            "inbound_enabled": bool(row["inbound_enabled"]),
            "version": int(row["version"]),
            "reason": row["reason"],
            "updated_at": row["updated_at"],
        }

    async def get_runtime_capabilities(
        self,
        *,
        tenant_id: str,
        config_id: Optional[str] = None,
    ) -> dict[str, bool]:
        """Expose only server-authoritative, fail-closed tenant UI capabilities."""

        from app.domain.services.telephony.inbound_transfer import (
            inbound_transfer_scope_available,
        )

        tenant_id = _uuid(tenant_id, "tenant_id")
        if config_id is not None:
            config_id = _uuid(config_id, "config_id")

        async with acquire_with_tenant(self._pool, None) as conn:
            platform_enabled = bool(
                await conn.fetchval(
                    "SELECT inbound_transfer_enabled FROM platform_runtime_controls WHERE id=1"
                )
            )
            configuration_owned = bool(
                config_id
                and await conn.fetchval(
                    """
                    SELECT EXISTS(
                        SELECT 1 FROM inbound_campaign_configs
                        WHERE id=$1::uuid AND tenant_id=$2::uuid
                    )
                    """,
                    config_id,
                    tenant_id,
                )
            )
        runtime_available = configuration_owned and inbound_transfer_scope_available(
            tenant_id=tenant_id, config_id=config_id
        )
        return {
            "transfer_runtime_available": runtime_available,
            "transfer_platform_enabled": platform_enabled,
            "transfer_configuration_available": runtime_available and platform_enabled,
        }

    async def set_tenant_controls(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        actor_role: str,
        inbound_enabled: bool,
        expected_version: int,
        reason: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        tenant_id = _uuid(tenant_id, "tenant_id")
        actor_id = _uuid(actor_id, "actor_id")
        operation = "inbound_tenant_controls.patch"
        scope = f"tenant:{tenant_id}"
        payload = {
            "inbound_enabled": inbound_enabled,
            "expected_version": expected_version,
            "reason": reason,
        }
        async with acquire_with_tenant(self._pool, None) as conn:
            replay = await self._claim_operation(
                conn,
                tenant_id=tenant_id,
                scope_key=scope,
                operation=operation,
                idempotency_key=idempotency_key,
                actor_id=actor_id,
                request_payload=payload,
            )
            if replay is not None:
                replay["idempotent_replay"] = True
                return replay
            current = await conn.fetchrow(
                "SELECT * FROM tenant_inbound_controls WHERE tenant_id=$1 FOR UPDATE",
                tenant_id,
            )
            if not current:
                if expected_version != 1:
                    raise InboundConflictError("Version conflict: current version is 1")
                row = await conn.fetchrow(
                    """
                    INSERT INTO tenant_inbound_controls
                        (tenant_id, inbound_enabled, version, reason, updated_by)
                    VALUES ($1, $2, 2, $3, $4)
                    RETURNING *
                    """,
                    tenant_id,
                    inbound_enabled,
                    reason,
                    actor_id,
                )
                before = {"inbound_enabled": False, "version": 1}
            else:
                if int(current["version"]) != expected_version:
                    raise InboundConflictError(
                        f"Version conflict: expected {expected_version}, current {current['version']}"
                    )
                row = await conn.fetchrow(
                    """
                    UPDATE tenant_inbound_controls
                    SET inbound_enabled=$2, version=version+1, reason=$3,
                        updated_by=$4, updated_at=NOW()
                    WHERE tenant_id=$1 AND version=$5
                    RETURNING *
                    """,
                    tenant_id,
                    inbound_enabled,
                    reason,
                    actor_id,
                    expected_version,
                )
                before = dict(current)
            result = {
                "inbound_enabled": bool(row["inbound_enabled"]),
                "version": int(row["version"]),
                "reason": row["reason"],
                "updated_at": row["updated_at"],
            }
            await self._audit(
                conn,
                tenant_id=tenant_id,
                event_type="tenant_inbound_controls_changed",
                actor_id=actor_id,
                actor_role=actor_role,
                resource_type="tenant_inbound_controls",
                resource_id=tenant_id,
                reason=reason,
                before=before,
                after=result,
                idempotency_key=idempotency_key,
            )
            await self._store_operation(
                conn,
                scope_key=scope,
                operation=operation,
                idempotency_key=idempotency_key,
                response=result,
                resource_type="tenant_inbound_controls",
                resource_id=tenant_id,
            )
            return result

    # ------------------------------------------------------------------
    # Platform operations
    # ------------------------------------------------------------------

    async def get_platform_controls(self) -> dict[str, Any]:
        async with acquire_with_tenant(self._pool, None) as conn:
            row = await conn.fetchrow(
                """
                SELECT inbound_enabled, inbound_recording_enabled,
                       inbound_transfer_enabled, inbound_settlement_enabled,
                       inbound_controls_version, inbound_controls_reason,
                       inbound_controls_updated_by, inbound_controls_updated_at
                FROM platform_runtime_controls WHERE id=1
                """
            )
        if not row:
            return {
                "inbound_enabled": False,
                "recording_enabled": False,
                "transfer_enabled": False,
                "settlement_enabled": False,
                "version": 1,
                "reason": "runtime controls unavailable",
                "updated_by": None,
                "updated_at": None,
            }
        return {
            "inbound_enabled": bool(row["inbound_enabled"]),
            "recording_enabled": bool(row["inbound_recording_enabled"]),
            "transfer_enabled": bool(row["inbound_transfer_enabled"]),
            "settlement_enabled": bool(row["inbound_settlement_enabled"]),
            "version": int(row["inbound_controls_version"]),
            "reason": row["inbound_controls_reason"],
            "updated_by": (
                str(row["inbound_controls_updated_by"])
                if row["inbound_controls_updated_by"]
                else None
            ),
            "updated_at": row["inbound_controls_updated_at"],
        }

    async def set_platform_controls(
        self,
        *,
        actor_id: str,
        actor_role: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        actor_id = _uuid(actor_id, "actor_id")
        operation = "platform_inbound_controls.patch"
        scope = "platform"
        expected = int(payload.get("expected_version") or 0)
        async with acquire_with_tenant(self._pool, None) as conn:
            replay = await self._claim_operation(
                conn,
                tenant_id=None,
                scope_key=scope,
                operation=operation,
                idempotency_key=idempotency_key,
                actor_id=actor_id,
                request_payload=payload,
            )
            if replay is not None:
                replay["idempotent_replay"] = True
                return replay
            current = await conn.fetchrow(
                "SELECT * FROM platform_runtime_controls WHERE id=1 FOR UPDATE"
            )
            if not current:
                raise InboundConflictError(
                    "Platform runtime controls are not initialized", code="controls_missing"
                )
            if int(current["inbound_controls_version"]) != expected:
                raise InboundConflictError(
                    f"Version conflict: expected {expected}, current {current['inbound_controls_version']}"
                )
            enabling_transfer = bool(payload["transfer_enabled"]) and not bool(
                current["inbound_transfer_enabled"]
            )
            if enabling_transfer:
                from app.domain.services.telephony.inbound_transfer import (
                    inbound_transfer_runtime_available,
                )

                if not inbound_transfer_runtime_available():
                    raise InboundConflictError(
                        "Inbound transfer cannot be enabled until the controlled "
                        "linked-leg runtime passes release validation",
                        code="transfer_runtime_unavailable",
                    )
            enabling_inbound = bool(payload["inbound_enabled"]) and not bool(
                current["inbound_enabled"]
            )
            if (
                enabling_inbound
                and os.getenv("ENVIRONMENT", "development").strip().lower() == "production"
            ):
                from app.core.inbound_startup import (
                    validate_live_production_inbound_adapter,
                    validate_production_inbound_database_role,
                    validate_production_inbound_state_backend,
                )
                from app.domain.services.telephony.adapter_registry import (
                    get_adapter,
                )
                from app.domain.services.telephony.state_backend import (
                    get_state_backend,
                )

                try:
                    await validate_production_inbound_database_role(
                        conn,
                        environment="production",
                        inbound_enabled=True,
                    )
                    validate_live_production_inbound_adapter(
                        environment="production",
                        inbound_enabled=True,
                        configured_adapter=os.getenv("TELEPHONY_ADAPTER", "auto"),
                        adapter=get_adapter(),
                    )
                    state_backend = get_state_backend()
                    validate_production_inbound_state_backend(
                        environment="production",
                        inbound_enabled=True,
                        configured_backend=os.getenv("TELEPHONY_STATE_BACKEND", "memory"),
                        state_backend=state_backend,
                    )
                    acquired = await state_backend.acquire_telephony_ownership_strict()
                    if not acquired:
                        raise RuntimeError("another process owns the telephony control plane")
                    await state_backend.start_heartbeat_strict()
                    if not state_backend.is_telephony_owner():
                        raise RuntimeError("strict telephony ownership could not be proven")
                except Exception as exc:  # Redis/adapter uncertainty is fail-closed
                    raise InboundConflictError(
                        "Inbound cannot be enabled until production restarts on "
                        "the admission-aware Asterisk adapter with strict Redis ownership",
                        code="inbound_runtime_restart_required",
                    ) from exc
            row = await conn.fetchrow(
                """
                UPDATE platform_runtime_controls
                SET inbound_enabled=$1,
                    inbound_recording_enabled=$2,
                    inbound_transfer_enabled=$3,
                    inbound_settlement_enabled=$4,
                    inbound_controls_reason=$5,
                    inbound_controls_updated_by=$6,
                    inbound_controls_updated_at=NOW(),
                    inbound_controls_version=inbound_controls_version+1,
                    updated_at=NOW()
                WHERE id=1 AND inbound_controls_version=$7
                RETURNING *
                """,
                bool(payload["inbound_enabled"]),
                bool(payload["recording_enabled"]),
                bool(payload["transfer_enabled"]),
                bool(payload["settlement_enabled"]),
                str(payload.get("reason") or "").strip(),
                actor_id,
                expected,
            )
            result = {
                "inbound_enabled": bool(row["inbound_enabled"]),
                "recording_enabled": bool(row["inbound_recording_enabled"]),
                "transfer_enabled": bool(row["inbound_transfer_enabled"]),
                "settlement_enabled": bool(row["inbound_settlement_enabled"]),
                "version": int(row["inbound_controls_version"]),
                "reason": row["inbound_controls_reason"],
                "updated_by": str(row["inbound_controls_updated_by"]),
                "updated_at": row["inbound_controls_updated_at"],
            }
            await self._audit(
                conn,
                tenant_id=None,
                event_type="platform_inbound_controls_changed",
                actor_id=actor_id,
                actor_role=actor_role,
                resource_type="platform_inbound_controls",
                resource_id=None,
                reason=result["reason"],
                before=dict(current),
                after=result,
                idempotency_key=idempotency_key,
            )
            await self._store_operation(
                conn,
                scope_key=scope,
                operation=operation,
                idempotency_key=idempotency_key,
                response=result,
                resource_type="platform_inbound_controls",
                resource_id=None,
            )
            return result

    async def admin_overview(self) -> dict[str, Any]:
        async with acquire_with_tenant(self._pool, None) as conn:
            counts = await conn.fetchrow(
                """
                SELECT
                    COUNT(*) FILTER (WHERE status='active') AS active_assignments,
                    COUNT(*) FILTER (WHERE status='paused') AS paused_assignments,
                    COUNT(*) FILTER (WHERE status='quarantined') AS quarantined_assignments
                FROM inbound_did_assignments
                WHERE status <> 'archived'
                """
            )
            denied = await conn.fetchval(
                """
                SELECT COUNT(*) FROM calls
                WHERE direction='inbound' AND admission_status='denied'
                  AND created_at >= NOW() - INTERVAL '24 hours'
                """
            )
        controls = await self.get_platform_controls()
        return {
            "active_assignments": int(counts["active_assignments"] or 0),
            "paused_assignments": int(counts["paused_assignments"] or 0),
            "quarantined_assignments": int(counts["quarantined_assignments"] or 0),
            "denied_last_24h": int(denied or 0),
            "controls": controls,
        }

    async def admin_list_assignments(
        self,
        *,
        tenant_id: Optional[str] = None,
        status: Optional[str] = None,
        search: Optional[str] = None,
    ) -> dict[str, Any]:
        clauses: list[str] = []
        args: list[Any] = []
        if tenant_id:
            args.append(_uuid(tenant_id, "tenant_id"))
            clauses.append(f"a.tenant_id = ${len(args)}")
        if status:
            if status not in {"active", "paused", "quarantined", "archived"}:
                raise InboundCampaignError("Invalid assignment status", code="invalid_status")
            args.append(status)
            clauses.append(f"a.status = ${len(args)}")
        else:
            clauses.append("a.status <> 'archived'")
        if search:
            args.append(f"%{search.strip()}%")
            clauses.append(
                f"(t.business_name ILIKE ${len(args)} OR c.name ILIKE ${len(args)} "
                f"OR a.canonical_did ILIKE ${len(args)})"
            )
        where = " AND ".join(clauses)
        async with acquire_with_tenant(self._pool, None) as conn:
            rows = await conn.fetch(
                _BUNDLE_SQL + f" WHERE {where} ORDER BY a.updated_at DESC LIMIT 500",
                *args,
            )
        items: list[dict[str, Any]] = []
        for raw in rows:
            row = dict(raw)
            items.append(
                {
                    "id": str(row["assignment_id"]),
                    "tenant_id": str(row["tenant_id"]),
                    "tenant_name": row["tenant_name"],
                    "masked_did": mask_did(row["canonical_did"]),
                    "campaign_id": str(row["campaign_id"]),
                    "campaign_name": row["campaign_name"],
                    "trunk_id": str(row["sip_trunk_id"]),
                    "status": row["assignment_status"],
                    "version": int(row["assignment_version"]),
                    "readiness": self._readiness(row),
                    "last_call_at": row["last_call_at"],
                    "last_error": row["last_error"],
                    "config_version": int(row["config_version"]),
                }
            )
        return {"items": items, "total": len(items)}

    async def admin_list_campaigns(
        self,
        *,
        tenant_id: Optional[str] = None,
        status: Optional[str] = None,
        search: Optional[str] = None,
    ) -> dict[str, Any]:
        clauses: list[str] = []
        args: list[Any] = []
        if tenant_id:
            args.append(_uuid(tenant_id, "tenant_id"))
            clauses.append(f"cfg.tenant_id = ${len(args)}")
        if status:
            if status not in {"draft", "active", "paused", "archived"}:
                raise InboundCampaignError("Invalid campaign status", code="invalid_status")
            args.append(status)
            clauses.append(f"cfg.status = ${len(args)}")
        else:
            clauses.append("cfg.status <> 'archived'")
        if search:
            args.append(f"%{search.strip()}%")
            clauses.append(
                f"(cfg.name ILIKE ${len(args)} OR c.name ILIKE ${len(args)} "
                f"OR t.business_name ILIKE ${len(args)})"
            )
        async with acquire_with_tenant(self._pool, None) as conn:
            rows = await conn.fetch(
                """
                SELECT cfg.id, t.business_name AS tenant_name
                FROM inbound_campaign_configs cfg
                JOIN campaigns c ON c.id=cfg.campaign_id AND c.tenant_id=cfg.tenant_id
                JOIN tenants t ON t.id=cfg.tenant_id
                WHERE """
                + " AND ".join(clauses)
                + " ORDER BY cfg.updated_at DESC LIMIT 500",
                *args,
            )
            items: list[dict[str, Any]] = []
            for row in rows:
                try:
                    bundle = await self._load_bundle(conn, str(row["id"]))
                except InboundNotFoundError:
                    # A target-owned config reserved for four-eye reassignment
                    # has no DID until approval. It is intentionally not
                    # routable and is omitted from the operational list.
                    continue
                item = self._serialize_bundle(bundle)
                item["tenant_name"] = row["tenant_name"]
                item["masked_did"] = mask_did(item.pop("did_number"))
                items.append(item)
        return {"items": items, "total": len(items)}

    async def set_assignment_quarantine(
        self,
        *,
        assignment_id: str,
        quarantined: bool,
        expected_version: int,
        reason: str,
        actor_id: str,
        actor_role: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        assignment_id = _uuid(assignment_id, "assignment_id")
        actor_id = _uuid(actor_id, "actor_id")
        action = "quarantine" if quarantined else "unquarantine"
        operation = f"platform_inbound_assignment.{action}:{assignment_id}"
        payload = {"expected_version": expected_version, "reason": reason}
        async with acquire_with_tenant(self._pool, None) as conn:
            replay = await self._claim_operation(
                conn,
                tenant_id=None,
                scope_key="platform",
                operation=operation,
                idempotency_key=idempotency_key,
                actor_id=actor_id,
                request_payload=payload,
            )
            if replay is not None:
                replay["idempotent_replay"] = True
                return replay
            assignment_ref = await conn.fetchrow(
                "SELECT config_id FROM inbound_did_assignments WHERE id=$1",
                assignment_id,
            )
            if not assignment_ref:
                raise InboundNotFoundError("Inbound DID assignment not found")
            # All competing paths lock config first and assignment second.
            config_lock = await conn.fetchrow(
                "SELECT id FROM inbound_campaign_configs WHERE id=$1 FOR UPDATE",
                assignment_ref["config_id"],
            )
            if not config_lock:
                raise InboundNotFoundError("Inbound campaign not found")
            row = await conn.fetchrow(
                "SELECT * FROM inbound_did_assignments WHERE id=$1 FOR UPDATE",
                assignment_id,
            )
            if not row:
                raise InboundNotFoundError("Inbound DID assignment not found")
            if int(row["version"]) != expected_version:
                raise InboundConflictError(
                    f"Version conflict: expected {expected_version}, current {row['version']}"
                )
            if row["status"] == "archived":
                raise InboundConflictError(
                    "Archived DID assignments are immutable",
                    code="assignment_archived",
                )
            before = dict(row)
            if quarantined:
                if row["status"] == "quarantined":
                    raise InboundConflictError(
                        "Assignment is already quarantined",
                        code="already_quarantined",
                    )
                prior = (
                    row["status"]
                    if row["status"] in {"active", "paused"}
                    else row["status_before_quarantine"]
                )
                updated = await conn.fetchrow(
                    """
                    UPDATE inbound_did_assignments
                    SET status='quarantined', status_before_quarantine=$2,
                        quarantine_reason=$3, version=version+1,
                        updated_by=$4, updated_at=NOW()
                    WHERE id=$1 AND version=$5 AND status IN ('active','paused')
                    RETURNING *
                    """,
                    assignment_id,
                    prior or "paused",
                    reason,
                    actor_id,
                    expected_version,
                )
            else:
                if row["status"] != "quarantined":
                    raise InboundConflictError(
                        "Assignment is not quarantined", code="not_quarantined"
                    )
                restore = row["status_before_quarantine"] or "paused"
                try:
                    updated = await conn.fetchrow(
                        """
                        UPDATE inbound_did_assignments
                        SET status=$2, status_before_quarantine=NULL,
                            quarantine_reason=NULL, version=version+1,
                            updated_by=$3, updated_at=NOW()
                        WHERE id=$1 AND version=$4 AND status='quarantined'
                        RETURNING *
                        """,
                        assignment_id,
                        restore,
                        actor_id,
                        expected_version,
                    )
                except asyncpg.UniqueViolationError as exc:
                    raise InboundConflictError(
                        "DID now belongs to another active assignment",
                        code="did_assignment_conflict",
                    ) from exc
            if not updated:
                raise InboundConflictError(
                    "DID assignment changed during quarantine update",
                    code="version_conflict",
                )
            result = {
                "id": str(updated["id"]),
                "status": updated["status"],
                "version": int(updated["version"]),
                "masked_did": mask_did(updated["canonical_did"]),
                "quarantine_reason": updated["quarantine_reason"],
            }
            await self._audit(
                conn,
                tenant_id=None,
                event_type=f"inbound_assignment_{action}d",
                actor_id=actor_id,
                actor_role=actor_role,
                resource_type="inbound_did_assignment",
                resource_id=assignment_id,
                reason=reason,
                before=before,
                after=dict(updated),
                metadata={
                    "tenant_id": str(row["tenant_id"]),
                    "did_ref": redact_did(row["canonical_did"]),
                },
                idempotency_key=idempotency_key,
            )
            await self._store_operation(
                conn,
                scope_key="platform",
                operation=operation,
                idempotency_key=idempotency_key,
                response=result,
                resource_type="inbound_did_assignment",
                resource_id=assignment_id,
            )
            return result

    @staticmethod
    def _serialize_reassignment(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "id": str(row["id"]),
            "assignment_id": str(row["assignment_id"]),
            "approved_assignment_id": (
                str(row["approved_assignment_id"]) if row.get("approved_assignment_id") else None
            ),
            "source_tenant_id": str(row.get("source_tenant_id") or row["tenant_id"]),
            "target_tenant_id": str(row["target_tenant_id"]),
            "target_campaign_id": str(row["target_campaign_id"]),
            "status": row["status"],
            "reason": row["reason"],
            "decision_reason": row.get("decision_reason"),
            "requested_by": str(row["requested_by"]),
            "approved_by": str(row["approved_by"]) if row.get("approved_by") else None,
            "requested_at": row["requested_at"],
            "decided_at": row.get("decided_at"),
        }

    async def create_reassignment_request(
        self,
        *,
        assignment_id: str,
        target_tenant_id: str,
        target_campaign_id: str,
        expected_version: int,
        reason: str,
        actor_id: str,
        actor_role: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        assignment_id = _uuid(assignment_id, "assignment_id")
        target_tenant_id = _uuid(target_tenant_id, "target_tenant_id")
        target_campaign_id = _uuid(target_campaign_id, "target_campaign_id")
        actor_id = _uuid(actor_id, "actor_id")
        operation = "platform_inbound_reassignment.create"
        request_payload = {
            "assignment_id": assignment_id,
            "target_tenant_id": target_tenant_id,
            "target_campaign_id": target_campaign_id,
            "expected_version": expected_version,
            "reason": reason,
        }
        async with acquire_with_tenant(self._pool, None) as conn:
            replay = await self._claim_operation(
                conn,
                tenant_id=None,
                scope_key="platform",
                operation=operation,
                idempotency_key=idempotency_key,
                actor_id=actor_id,
                request_payload=request_payload,
            )
            if replay is not None:
                replay["idempotent_replay"] = True
                return replay
            assignment_ref = await conn.fetchrow(
                "SELECT config_id FROM inbound_did_assignments WHERE id=$1",
                assignment_id,
            )
            if not assignment_ref:
                raise InboundNotFoundError("Inbound DID assignment not found")
            source_config_lock = await conn.fetchrow(
                "SELECT id FROM inbound_campaign_configs WHERE id=$1 FOR UPDATE",
                assignment_ref["config_id"],
            )
            if not source_config_lock:
                raise InboundNotFoundError("Source inbound campaign not found")
            assignment = await conn.fetchrow(
                "SELECT * FROM inbound_did_assignments WHERE id=$1 FOR UPDATE",
                assignment_id,
            )
            if not assignment:
                raise InboundNotFoundError("Inbound DID assignment not found")
            if int(assignment["version"]) != expected_version:
                raise InboundConflictError(
                    f"Version conflict: expected {expected_version}, current {assignment['version']}"
                )
            if str(assignment["tenant_id"]) == target_tenant_id:
                raise InboundConflictError(
                    "Target tenant already owns this assignment", code="same_tenant"
                )
            pending = await conn.fetchval(
                """
                SELECT id FROM inbound_reassignment_requests
                WHERE assignment_id=$1 AND status='pending'
                """,
                assignment_id,
            )
            if pending:
                raise InboundConflictError(
                    "A reassignment request is already pending", code="reassignment_pending"
                )
            campaign = await conn.fetchrow(
                """
                SELECT id, tenant_id, direction, status
                FROM campaigns
                WHERE id=$1 AND tenant_id=$2
                """,
                target_campaign_id,
                target_tenant_id,
            )
            if not campaign:
                raise InboundNotFoundError("Target campaign not found")
            if (
                str(campaign["id"]) != target_campaign_id
                or str(campaign["tenant_id"]) != target_tenant_id
            ):
                raise InboundConflictError(
                    "Target campaign ownership is inconsistent",
                    code="target_campaign_mismatch",
                )
            if campaign["direction"] != "inbound":
                raise InboundConflictError(
                    "Target campaign must already be inbound",
                    code="target_campaign_not_inbound",
                )
            if campaign["status"] in {"completed", "cancelled", "deleted"}:
                raise InboundConflictError(
                    "Target campaign is terminal and cannot receive a DID",
                    code="target_campaign_terminal",
                )
            target_cfg = await conn.fetchrow(
                """
                SELECT id, tenant_id, campaign_id, status
                FROM inbound_campaign_configs
                WHERE campaign_id=$1 AND tenant_id=$2
                """,
                target_campaign_id,
                target_tenant_id,
            )
            if not target_cfg:
                raise InboundNotFoundError(
                    "Target tenant must create its own inbound campaign configuration"
                )
            if (
                str(target_cfg["tenant_id"]) != target_tenant_id
                or str(target_cfg["campaign_id"]) != target_campaign_id
            ):
                raise InboundConflictError(
                    "Target campaign configuration ownership is inconsistent",
                    code="target_config_mismatch",
                )
            if target_cfg["status"] == "archived":
                raise InboundConflictError(
                    "Target inbound campaign configuration is archived",
                    code="target_config_archived",
                )
            occupied = await conn.fetchval(
                """
                SELECT EXISTS(
                    SELECT 1 FROM inbound_did_assignments
                    WHERE config_id=$1 AND status <> 'archived'
                )
                """,
                target_cfg["id"],
            )
            if occupied:
                raise InboundConflictError(
                    "Target inbound campaign already has a DID",
                    code="target_campaign_occupied",
                )
            prior = (
                assignment["status"] if assignment["status"] in {"active", "paused"} else "paused"
            )
            quarantined = await conn.fetchrow(
                """
                UPDATE inbound_did_assignments
                SET status='quarantined', status_before_quarantine=$2,
                    quarantine_reason=$3, version=version+1,
                    updated_by=$4, updated_at=NOW()
                WHERE id=$1 AND version=$5 RETURNING *
                """,
                assignment_id,
                prior,
                reason,
                actor_id,
                expected_version,
            )
            request_row = await conn.fetchrow(
                """
                INSERT INTO inbound_reassignment_requests (
                    tenant_id, source_tenant_id, assignment_id, target_tenant_id,
                    target_campaign_id, target_config_id,
                    expected_assignment_version, reason, requested_by,
                    idempotency_key
                ) VALUES ($1,$1,$2,$3,$4,$5,$6,$7,$8,$9)
                RETURNING *
                """,
                assignment["tenant_id"],
                assignment_id,
                target_tenant_id,
                target_campaign_id,
                target_cfg["id"],
                quarantined["version"],
                reason,
                actor_id,
                idempotency_key,
            )
            result = self._serialize_reassignment(dict(request_row))
            await self._audit(
                conn,
                tenant_id=None,
                event_type="inbound_reassignment_requested",
                actor_id=actor_id,
                actor_role=actor_role,
                resource_type="inbound_reassignment_request",
                resource_id=str(request_row["id"]),
                reason=reason,
                before=dict(assignment),
                after={"request": result, "assignment": dict(quarantined)},
                metadata={"did_ref": redact_did(assignment["canonical_did"])},
                idempotency_key=idempotency_key,
            )
            await self._store_operation(
                conn,
                scope_key="platform",
                operation=operation,
                idempotency_key=idempotency_key,
                response=result,
                resource_type="inbound_reassignment_request",
                resource_id=str(request_row["id"]),
                status_code=201,
            )
            return result

    async def list_reassignment_requests(self, *, status: str = "pending") -> dict[str, Any]:
        if status not in {"pending", "approved", "rejected", "cancelled"}:
            raise InboundCampaignError("Invalid reassignment status", code="invalid_status")
        async with acquire_with_tenant(self._pool, None) as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM inbound_reassignment_requests
                WHERE status=$1 ORDER BY requested_at DESC LIMIT 500
                """,
                status,
            )
        items = [self._serialize_reassignment(dict(row)) for row in rows]
        return {"items": items, "total": len(items)}

    async def approve_reassignment(
        self,
        *,
        request_id: str,
        reason: str,
        actor_id: str,
        actor_role: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        request_id = _uuid(request_id, "request_id")
        actor_id = _uuid(actor_id, "actor_id")
        operation = f"platform_inbound_reassignment.approve:{request_id}"
        async with acquire_with_tenant(self._pool, None) as conn:
            replay = await self._claim_operation(
                conn,
                tenant_id=None,
                scope_key="platform",
                operation=operation,
                idempotency_key=idempotency_key,
                actor_id=actor_id,
                request_payload={"reason": reason},
            )
            if replay is not None:
                replay["idempotent_replay"] = True
                return replay
            req = await conn.fetchrow(
                "SELECT * FROM inbound_reassignment_requests WHERE id=$1 FOR UPDATE",
                request_id,
            )
            if not req:
                raise InboundNotFoundError("Reassignment request not found")
            if req["status"] != "pending":
                raise InboundConflictError(
                    f"Reassignment is already {req['status']}", code="already_decided"
                )
            if str(req["requested_by"]) == actor_id:
                raise InboundConflictError(
                    "A different platform administrator must approve this request",
                    code="four_eye_required",
                )
            assignment_ref = await conn.fetchrow(
                "SELECT config_id FROM inbound_did_assignments WHERE id=$1",
                req["assignment_id"],
            )
            if not assignment_ref:
                raise InboundConflictError(
                    "Assignment is no longer in the quarantined review state",
                    code="assignment_state_changed",
                )
            source_config_lock = await conn.fetchrow(
                "SELECT id FROM inbound_campaign_configs WHERE id=$1 FOR UPDATE",
                assignment_ref["config_id"],
            )
            if not source_config_lock:
                raise InboundConflictError(
                    "Source campaign is no longer available",
                    code="assignment_state_changed",
                )
            assignment = await conn.fetchrow(
                "SELECT * FROM inbound_did_assignments WHERE id=$1 FOR UPDATE",
                req["assignment_id"],
            )
            if not assignment or assignment["status"] != "quarantined":
                raise InboundConflictError(
                    "Assignment is no longer in the quarantined review state",
                    code="assignment_state_changed",
                )
            if int(assignment["version"]) != int(req["expected_assignment_version"]):
                raise InboundConflictError(
                    "Assignment changed after the reassignment request",
                    code="version_conflict",
                )
            target_campaign = await conn.fetchrow(
                """
                SELECT id, tenant_id, direction, status
                FROM campaigns
                WHERE id=$1 AND tenant_id=$2
                FOR UPDATE
                """,
                req["target_campaign_id"],
                req["target_tenant_id"],
            )
            if not target_campaign:
                raise InboundConflictError(
                    "Target campaign ownership changed after the request",
                    code="target_campaign_changed",
                )
            if str(target_campaign["id"]) != str(req["target_campaign_id"]) or str(
                target_campaign["tenant_id"]
            ) != str(req["target_tenant_id"]):
                raise InboundConflictError(
                    "Target campaign ownership changed after the request",
                    code="target_campaign_changed",
                )
            if target_campaign["direction"] != "inbound":
                raise InboundConflictError(
                    "Target campaign is no longer inbound",
                    code="target_campaign_not_inbound",
                )
            if target_campaign["status"] in {"completed", "cancelled", "deleted"}:
                raise InboundConflictError(
                    "Target campaign became terminal after the request",
                    code="target_campaign_terminal",
                )
            target_config = await conn.fetchrow(
                """
                SELECT id, tenant_id, campaign_id, status
                FROM inbound_campaign_configs
                WHERE id=$1 AND tenant_id=$2 AND campaign_id=$3
                FOR UPDATE
                """,
                req["target_config_id"],
                req["target_tenant_id"],
                req["target_campaign_id"],
            )
            if not target_config:
                raise InboundConflictError(
                    "Target campaign configuration changed after the request",
                    code="target_config_changed",
                )
            if (
                str(target_config["id"]) != str(req["target_config_id"])
                or str(target_config["tenant_id"]) != str(req["target_tenant_id"])
                or str(target_config["campaign_id"]) != str(req["target_campaign_id"])
            ):
                raise InboundConflictError(
                    "Target campaign configuration changed after the request",
                    code="target_config_changed",
                )
            if target_config["status"] == "archived":
                raise InboundConflictError(
                    "Target campaign configuration was archived after the request",
                    code="target_config_archived",
                )
            target_occupied = await conn.fetchval(
                """
                SELECT EXISTS(
                    SELECT 1 FROM inbound_did_assignments
                    WHERE config_id=$1 AND status <> 'archived'
                )
                """,
                req["target_config_id"],
            )
            if target_occupied:
                raise InboundConflictError(
                    "Target inbound campaign received another DID after the request",
                    code="target_campaign_occupied",
                )
            trunks = await conn.fetch(
                """
                SELECT id, is_active, direction, metadata,
                       live_registration_status, live_status_detail,
                       live_status_checked_at
                FROM tenant_sip_trunks
                WHERE tenant_id=$1 AND is_active=TRUE AND direction IN ('inbound','both')
                ORDER BY id LIMIT 2
                """,
                req["target_tenant_id"],
            )
            if len(trunks) != 1:
                raise InboundConflictError(
                    "Target tenant must have exactly one active inbound trunk for approval",
                    code="target_trunk_ambiguous" if trunks else "target_trunk_missing",
                )
            target_trunk_runtime = evaluate_trunk_runtime(trunks[0], require_inbound=True)
            if not target_trunk_runtime.ready:
                raise InboundConflictError(
                    "Target tenant's inbound trunk is not ready: " f"{target_trunk_runtime.detail}",
                    code="target_trunk_not_ready",
                )
            target_phone = await conn.fetchrow(
                """
                SELECT * FROM tenant_phone_numbers
                WHERE tenant_id=$1 AND e164=$2
                """,
                req["target_tenant_id"],
                assignment["canonical_did"],
            )
            if target_phone:
                if target_phone["status"] != "verified":
                    raise InboundConflictError(
                        "Target tenant has the DID but it is not verified",
                        code="target_did_not_verified",
                    )
                target_phone_id = target_phone["id"]
            else:
                # Preserve the source phone row because historical calls hold
                # a tenant-safe (called_did_id, tenant_id) reference to it.
                # The four-eye approval copies the existing verification
                # evidence into a new target-owned row; it never self-verifies
                # an arbitrary DID supplied by the request.
                source_phone = await conn.fetchrow(
                    "SELECT * FROM tenant_phone_numbers WHERE id=$1 FOR UPDATE",
                    assignment["phone_number_id"],
                )
                if not source_phone or source_phone["status"] != "verified":
                    raise InboundConflictError(
                        "Source DID verification is no longer valid",
                        code="source_did_not_verified",
                    )
                target_phone = await conn.fetchrow(
                    """
                    INSERT INTO tenant_phone_numbers (
                        tenant_id, e164, provider, status,
                        verification_method, verification_sent_at, verified_at,
                        verified_by, stir_shaken_token, label, metadata
                    ) VALUES (
                        $1,$2,$3,'verified',$4,$5,$6,$7,$8,$9,$10::jsonb
                    )
                    RETURNING *
                    """,
                    req["target_tenant_id"],
                    source_phone["e164"],
                    source_phone["provider"],
                    source_phone["verification_method"],
                    source_phone["verification_sent_at"],
                    source_phone["verified_at"],
                    source_phone["verified_by"],
                    source_phone["stir_shaken_token"],
                    source_phone["label"],
                    json.dumps(
                        {
                            **_json_obj(source_phone["metadata"]),
                            "verification_copied_from_phone_id": str(source_phone["id"]),
                            "inbound_reassigned_from_tenant": str(assignment["tenant_id"]),
                            "reassignment_request_id": request_id,
                        }
                    ),
                )
                target_phone_id = target_phone["id"]
            await conn.execute(
                """
                UPDATE inbound_campaign_configs
                SET status='paused', version=version+1, updated_by=$2, updated_at=NOW()
                WHERE id=$1
                """,
                assignment["config_id"],
                actor_id,
            )
            closed = await conn.fetchrow(
                """
                UPDATE inbound_did_assignments
                SET status='archived', valid_to=COALESCE(valid_to,NOW()),
                    status_before_quarantine=NULL, quarantine_reason=NULL,
                    version=version+1, updated_by=$2, updated_at=NOW()
                WHERE id=$1 AND tenant_id=$3 RETURNING *
                """,
                assignment["id"],
                actor_id,
                assignment["tenant_id"],
            )
            if not closed:
                raise InboundConflictError(
                    "Source assignment changed during approval",
                    code="version_conflict",
                )
            try:
                moved = await conn.fetchrow(
                    """
                    INSERT INTO inbound_did_assignments (
                        tenant_id, phone_number_id, campaign_id, config_id,
                        sip_trunk_id, canonical_did, status, created_by, updated_by
                    ) VALUES ($1,$2,$3,$4,$5,$6,'paused',$7,$7)
                    RETURNING *
                    """,
                    req["target_tenant_id"],
                    target_phone_id,
                    req["target_campaign_id"],
                    req["target_config_id"],
                    trunks[0]["id"],
                    assignment["canonical_did"],
                    actor_id,
                )
            except asyncpg.UniqueViolationError as exc:
                raise InboundConflictError(
                    "DID already has a live assignment",
                    code="did_assignment_conflict",
                ) from exc
            if str(target_phone_id) != str(assignment["phone_number_id"]):
                await conn.execute(
                    """
                    UPDATE tenant_phone_numbers
                    SET status='revoked', updated_at=NOW(),
                        metadata=metadata || $2::jsonb
                    WHERE id=$1
                    """,
                    assignment["phone_number_id"],
                    json.dumps(
                        {
                            "revoked_by_inbound_reassignment": request_id,
                            "reassigned_to_tenant": str(req["target_tenant_id"]),
                        }
                    ),
                )
            await conn.execute(
                """
                UPDATE inbound_campaign_configs
                SET status='paused', version=version+1, updated_by=$2, updated_at=NOW()
                WHERE id=$1
                """,
                req["target_config_id"],
                actor_id,
            )
            decided = await conn.fetchrow(
                """
                UPDATE inbound_reassignment_requests
                SET approved_assignment_id=$4, status='approved', approved_by=$2,
                    decision_reason=$3, decided_at=NOW()
                WHERE id=$1 RETURNING *
                """,
                request_id,
                actor_id,
                reason,
                moved["id"],
            )
            result = self._serialize_reassignment(dict(decided))
            await self._audit(
                conn,
                tenant_id=None,
                event_type="inbound_reassignment_approved",
                actor_id=actor_id,
                actor_role=actor_role,
                resource_type="inbound_reassignment_request",
                resource_id=request_id,
                reason=reason,
                before={"request": dict(req), "assignment": dict(assignment)},
                after={
                    "request": result,
                    "source_assignment": dict(closed),
                    "target_assignment": dict(moved),
                },
                metadata={"did_ref": redact_did(assignment["canonical_did"])},
                idempotency_key=idempotency_key,
            )
            await self._store_operation(
                conn,
                scope_key="platform",
                operation=operation,
                idempotency_key=idempotency_key,
                response=result,
                resource_type="inbound_reassignment_request",
                resource_id=request_id,
            )
            return result
