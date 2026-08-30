"""Atomic, pre-answer admission for inbound carrier calls.

This module has no telephony side effects.  It decides, persists and reserves;
the adapter may answer only when ``InboundAdmissionDecision.allowed`` is true.
All failures are represented by stable, bounded reasons and fail closed.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import uuid
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Optional

import asyncpg

from app.core.db_utils import acquire_with_tenant
from app.domain.services.call_status import TERMINAL_CALL_STATUSES
from app.domain.services.telephony.business_hours import evaluate_business_hours
from app.domain.services.telephony.inbound_router import (
    is_active_inbound_campaign_status,
    normalize_did,
    parse_tenant_from_context,
    redact_did,
)
from app.domain.services.telephony.inbound_transfer import (
    fetch_tenant_accounted_usage_seconds,
    inbound_transfer_destination_approved,
    inbound_transfer_runtime_available,
    inbound_transfer_scope_available,
)
from app.domain.services.telephony.trunk_runtime import evaluate_trunk_runtime
from app.domain.services.telephony_concurrency_limiter import (
    LeaseKind,
    TelephonyConcurrencyLimiter,
)

logger = logging.getLogger(__name__)


_PROVIDER_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,31}$")
_OUTCOME_RE = re.compile(r"^[a-z][a-z0-9_]{0,99}$")
_ISO_4217_CODES = frozenset(
    """
    AED AFN ALL AMD ANG AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF BMD
    BND BOB BOV BRL BSD BTN BWP BYN BZD CAD CDF CHE CHF CHW CLF CLP CNY
    COP COU CRC CUC CUP CVE CZK DJF DKK DOP DZD EGP ERN ETB EUR FJD FKP
    GBP GEL GHS GIP GMD GNF GTQ GYD HKD HNL HRK HTG HUF IDR ILS INR IQD
    IRR ISK JMD JOD JPY KES KGS KHR KMF KPW KRW KWD KYD KZT LAK LBP LKR
    LRD LSL LYD MAD MDL MGA MKD MMK MNT MOP MRO MRU MUR MVR MWK MXN MXV
    MYR MZN NAD NGN NIO NOK NPR NZD OMR PAB PEN PGK PHP PKR PLN PYG QAR
    RON RSD RUB RWF SAR SBD SCR SDG SEK SGD SHP SLE SLL SOS SRD SSP STD
    STN SVC SYP SZL THB TJS TMT TND TOP TRY TTD TWD TZS UAH UGX USD USN
    UYI UYU UYW UZS VED VEF VES VND VUV WST XAF XAG XAU XBA XBB XBC XBD
    XCD XCG XDR XOF XPD XPF XPT XSU XUA YER ZAR ZMW ZWG ZWL
    """.split()
)
_CALL_COST_QUANTUM = Decimal("0.0001")
_CALL_COST_MAX = Decimal("999999.9999")
_TERMINAL_STATUSES = frozenset(TERMINAL_CALL_STATUSES)
_ANSWER_AMBIGUOUS_TERMINAL_REASON = "process_restart_answer_ambiguous"
_ANSWER_AMBIGUOUS_HOLD_REASON = "provider_answer_ambiguous"
_RESOLVABLE_HOLD_EVIDENCE = {
    "provider_answer_ambiguous": "carrier_cdr",
    "usage_exceeded_reservation": "provider_usage_record",
}


@dataclass(frozen=True)
class InboundAdmissionRequest:
    provider: str
    provider_call_id: str
    called_did: str
    caller_ani: Optional[str] = None
    ingress: str = "asterisk"
    context: Optional[str] = None
    request_id: Optional[str] = None
    reservation_seconds: int = 60
    metadata: Mapping[str, Any] = field(default_factory=dict)
    provider_event_id: Optional[str] = None


@dataclass(frozen=True)
class InboundAdmissionDecision:
    allowed: bool
    reason: str
    call_id: Optional[str] = None
    talklee_call_id: Optional[str] = None
    tenant_id: Optional[str] = None
    campaign_id: Optional[str] = None
    config_id: Optional[str] = None
    assignment_id: Optional[str] = None
    trunk_id: Optional[str] = None
    route_version: Optional[int] = None
    config_version: Optional[int] = None
    opening_mode: Optional[str] = None
    config_snapshot: Mapping[str, Any] = field(default_factory=dict)
    concurrency_lease_id: Optional[str] = None
    usage_reservation_id: Optional[str] = None
    is_replay: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "call_id": self.call_id,
            "talklee_call_id": self.talklee_call_id,
            "tenant_id": self.tenant_id,
            "campaign_id": self.campaign_id,
            "config_id": self.config_id,
            "assignment_id": self.assignment_id,
            "trunk_id": self.trunk_id,
            "route_version": self.route_version,
            "config_version": self.config_version,
            "opening_mode": self.opening_mode,
            "config_snapshot": dict(self.config_snapshot),
            "concurrency_lease_id": self.concurrency_lease_id,
            "usage_reservation_id": self.usage_reservation_id,
            "is_replay": self.is_replay,
        }


@dataclass(frozen=True)
class InboundFinalizationRequest:
    call_id: str
    provider: str
    provider_call_id: str
    terminal_status: str
    duration_seconds: int
    cost: Optional[Decimal | float] = None
    outcome: Optional[str] = None
    reason: Optional[str] = None
    request_id: Optional[str] = None


@dataclass(frozen=True)
class InboundFinalizationResult:
    finalized: bool
    reason: str
    call_id: str
    billing_status: str
    lease_released: bool
    usage_transaction_id: Optional[str] = None
    is_replay: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "finalized": self.finalized,
            "reason": self.reason,
            "call_id": self.call_id,
            "billing_status": self.billing_status,
            "lease_released": self.lease_released,
            "usage_transaction_id": self.usage_transaction_id,
            "is_replay": self.is_replay,
        }


@dataclass(frozen=True)
class InboundHoldResolutionRequest:
    call_id: str
    tenant_id: str
    hold_reason: str
    decision: str
    evidence_type: str
    evidence_reference: str
    evidence_sha256: str
    adjudication_reason: str
    authoritative_duration_seconds: Optional[int]
    authoritative_cost: Optional[Decimal | float]
    authoritative_currency: Optional[str]
    actor_id: str
    actor_role: str
    request_id: str
    approval_action: Optional[str] = None
    approval_request_id: Optional[str] = None


@dataclass(frozen=True)
class InboundHoldResolutionResult:
    call_id: str
    tenant_id: str
    hold_reason: str
    decision: str
    billing_status: str
    duration_seconds: int
    usage_transaction_id: Optional[str]
    evidence_type: str
    evidence_reference: str
    evidence_sha256: str
    authoritative_currency: Optional[str] = None
    workflow_status: str = "resolved"
    approval_request_id: Optional[str] = None
    requested_by: Optional[str] = None
    approved_by: Optional[str] = None
    is_replay: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class InboundHoldResolutionConflictError(RuntimeError):
    """The held row or immutable resolution disagrees with the request."""


class InboundHoldResolutionNotFoundError(LookupError):
    """No inbound call exists in the explicitly selected tenant."""


def _json_obj(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


def _provider(value: str) -> Optional[str]:
    normalized = str(value or "").strip().lower()
    return normalized if _PROVIDER_RE.fullmatch(normalized) else None


def _call_id(value: str) -> Optional[str]:
    normalized = str(value or "").strip()
    return normalized if 1 <= len(normalized) <= 255 else None


def _private_ani(raw: Optional[str]) -> tuple[Optional[str], bool]:
    if not raw:
        return None, True
    marker = str(raw).strip().lower()
    if marker in {"anonymous", "private", "restricted", "unknown", "unavailable"}:
        return None, True
    normalized = normalize_did(raw)
    return (normalized, normalized is None)


class InboundAdmissionService:
    """Database authority for inbound pre-answer admission and teardown."""

    def __init__(
        self,
        db_pool: Any,
        concurrency_limiter: Optional[TelephonyConcurrencyLimiter] = None,
    ):
        self._pool = db_pool
        self._limiter = concurrency_limiter or TelephonyConcurrencyLimiter()

    @staticmethod
    def _metric(provider: str, reason: str) -> None:
        try:
            from app.infrastructure.metrics.inbound_metrics import record_admission_decision

            record_admission_decision(provider, reason)
        except Exception:
            pass

    @staticmethod
    def _usage_metric(event: str, result: str) -> None:
        try:
            from app.infrastructure.metrics.inbound_metrics import record_usage_event

            record_usage_event(event, result)
        except Exception:
            pass

    def _deny(self, provider: str, reason: str, **kwargs: Any) -> InboundAdmissionDecision:
        self._metric(provider, reason)
        return InboundAdmissionDecision(allowed=False, reason=reason, **kwargs)

    @staticmethod
    def _decision_from_call(row: Mapping[str, Any], *, replay: bool) -> InboundAdmissionDecision:
        snapshot = _json_obj(row.get("route_snapshot"))
        route = _json_obj(snapshot.get("route"))
        inbound_config = _json_obj(snapshot.get("inbound_config"))
        return InboundAdmissionDecision(
            allowed=row.get("admission_status") == "allowed",
            reason="duplicate_replay" if replay else str(row.get("admission_reason") or "allowed"),
            call_id=str(row["id"]),
            talklee_call_id=row.get("talklee_call_id"),
            tenant_id=str(row["tenant_id"]),
            campaign_id=str(row["campaign_id"]),
            config_id=str(route.get("config_id")) if route.get("config_id") else None,
            assignment_id=str(row["assignment_id"]) if row.get("assignment_id") else None,
            trunk_id=str(route.get("sip_trunk_id")) if route.get("sip_trunk_id") else None,
            route_version=int(row["route_version"]) if row.get("route_version") else None,
            config_version=int(row["config_version"]) if row.get("config_version") else None,
            opening_mode=inbound_config.get("opening_mode"),
            config_snapshot=snapshot,
            concurrency_lease_id=(
                str(row["concurrency_lease_id"]) if row.get("concurrency_lease_id") else None
            ),
            usage_reservation_id=(
                str(route.get("usage_reservation_id"))
                if route.get("usage_reservation_id")
                else None
            ),
            is_replay=replay,
        )

    async def _existing_call(
        self,
        conn: asyncpg.Connection,
        *,
        provider: str,
        provider_call_id: str,
        provider_event_id: Optional[str],
    ) -> Optional[Mapping[str, Any]]:
        row = await conn.fetchrow(
            """
            SELECT c.*,
                   (
                       c.admission_status='allowed'
                       AND c.processing_status='active'
                       AND c.billing_status='reserved'
                       AND c.reserved_seconds >= 60
                       AND EXISTS (
                           SELECT 1
                           FROM tenant_telephony_concurrency_leases lease
                           JOIN tenant_telephony_concurrency_policies policy
                             ON policy.id=lease.policy_id
                            AND policy.tenant_id=lease.tenant_id
                           WHERE lease.id=c.concurrency_lease_id
                             AND lease.tenant_id=c.tenant_id
                             AND lease.call_id=c.id
                             AND lease.lease_kind='call'
                             AND lease.state='active'
                             AND lease.released_at IS NULL
                             AND lease.last_heartbeat_at >= NOW() - (
                                 policy.lease_ttl_seconds
                                 + policy.heartbeat_grace_seconds
                             ) * INTERVAL '1 second'
                       )
                       AND EXISTS (
                           SELECT 1
                           FROM inbound_usage_transactions reserve
                           WHERE reserve.call_id=c.id
                             AND reserve.tenant_id=c.tenant_id
                             AND reserve.call_leg_id IS NULL
                             AND reserve.transaction_type='reserve'
                             AND reserve.id::text=(
                                 c.route_snapshot->'route'->>'usage_reservation_id'
                             )
                             AND reserve.quantity_seconds=c.reserved_seconds
                       )
                       AND NOT EXISTS (
                           SELECT 1
                           FROM inbound_usage_transactions terminal
                           WHERE terminal.call_id=c.id
                             AND terminal.call_leg_id IS NULL
                             AND terminal.transaction_type IN (
                                 'finalize','release','reverse'
                             )
                       )
                   ) AS replay_state_live
            FROM calls c
            WHERE c.direction='inbound'
              AND c.provider=$1
              AND (
                    c.provider_call_id=$2
                    OR ($3::text IS NOT NULL AND c.provider_event_id=$3)
              )
            ORDER BY c.created_at
            LIMIT 1
            FOR UPDATE OF c
            """,
            provider,
            provider_call_id,
            provider_event_id,
        )
        return dict(row) if row else None

    async def record_pre_row_rejection(
        self,
        *,
        provider: str,
        provider_call_id: str,
        called_did: Optional[str],
        caller_ani: Optional[str],
        ingress: str,
        reason: str,
    ) -> bool:
        """Persist a non-billable inbound denial that has no ``calls`` row.

        Tenant ownership is derived again from the DID, never trusted from the
        PBX context. Exactly one currently-active assignment is required. For
        unknown or ambiguous DIDs the platform keeps only the called business
        number and reason; unowned caller ANI is deliberately discarded.

        Returns ``True`` both for a new row and an idempotent replay of the
        same provider identity. Storage failures raise so the adapter can make
        the observability gap loud while still rejecting the PBX channel.
        """

        normalized_provider = _provider(provider)
        normalized_call_id = _call_id(provider_call_id)
        normalized_reason = str(reason or "").strip().lower()[:96]
        if not normalized_provider or not normalized_call_id or not normalized_reason:
            raise ValueError("provider, provider_call_id and reason are required")

        did = normalize_did(called_did)
        normalized_ani, ani_private = _private_ani(caller_ani)
        normalized_ingress = str(ingress or "asterisk").strip()[:64] or "asterisk"

        # A denial may precede tenant resolution by definition. Platform
        # bypass is therefore required for the ownership lookup and for
        # inserting the tenant_id=NULL audit row used for unknown DIDs.
        async with acquire_with_tenant(self._pool, None) as conn:
            routes: list[Mapping[str, Any]] = []
            if did:
                routes = list(
                    await conn.fetch(
                        """
                        SELECT a.tenant_id, a.campaign_id, a.config_id,
                               a.id AS assignment_id
                        FROM inbound_did_assignments a
                        WHERE a.canonical_did=$1
                          AND a.status='active'
                          AND a.valid_from <= NOW()
                          AND (a.valid_to IS NULL OR a.valid_to > NOW())
                        ORDER BY a.id
                        LIMIT 2
                        """,
                        did,
                    )
                )

            route = dict(routes[0]) if len(routes) == 1 else {}
            tenant_id = str(route["tenant_id"]) if route.get("tenant_id") else None
            # Never retain caller identity without a single tenant owner.
            stored_ani = normalized_ani if tenant_id and not ani_private else None
            stored_ani_private = bool(ani_private or not tenant_id)

            inserted = await conn.fetchrow(
                """
                INSERT INTO inbound_rejections (
                    provider, provider_call_id, tenant_id, campaign_id,
                    inbound_config_id, assignment_id, called_did, caller_ani,
                    caller_ani_private, ingress, reason
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
                ON CONFLICT (provider, provider_call_id) DO NOTHING
                RETURNING id
                """,
                normalized_provider,
                normalized_call_id,
                tenant_id,
                str(route["campaign_id"]) if route.get("campaign_id") else None,
                str(route["config_id"]) if route.get("config_id") else None,
                str(route["assignment_id"]) if route.get("assignment_id") else None,
                did,
                stored_ani,
                stored_ani_private,
                normalized_ingress,
                normalized_reason,
            )

            # Traffic-proportional bounded retention keeps hostile/random-DID
            # scans from growing this pre-auth audit surface forever.
            await conn.execute(
                """
                WITH expired AS (
                    SELECT id
                    FROM inbound_rejections
                    WHERE retention_until < NOW()
                    ORDER BY retention_until, id
                    LIMIT 100
                )
                DELETE FROM inbound_rejections r
                USING expired
                WHERE r.id=expired.id
                """
            )

        logger.info(
            "inbound_pre_row_rejection_recorded provider=%s call=%s "
            "tenant=%s reason=%s inserted=%s",
            normalized_provider,
            normalized_call_id[:12],
            tenant_id[:8] if tenant_id else "platform",
            normalized_reason,
            bool(inserted),
        )
        return True

    async def admit(self, request: InboundAdmissionRequest) -> InboundAdmissionDecision:
        provider = _provider(request.provider)
        provider_call_id = _call_id(request.provider_call_id)
        did = normalize_did(request.called_did)
        if not provider:
            return self._deny("other", "invalid_provider")
        if not provider_call_id:
            return self._deny(provider, "invalid_provider_call_id")
        if not did:
            return self._deny(provider, "invalid_did")
        try:
            requested_reservation_seconds = int(request.reservation_seconds)
        except (TypeError, ValueError, OverflowError):
            return self._deny(provider, "invalid_reservation")
        if not 1 <= requested_reservation_seconds <= 14_400:
            return self._deny(provider, "invalid_reservation")
        context_tenant = parse_tenant_from_context(request.context)
        caller_ani, caller_private = _private_ani(request.caller_ani)
        ingress = str(request.ingress or "asterisk").strip()[:64] or "asterisk"
        provider_event_id = (
            _call_id(request.provider_event_id) if request.provider_event_id else None
        )

        try:
            async with acquire_with_tenant(self._pool, None) as conn:
                existing = await self._existing_call(
                    conn,
                    provider=provider,
                    provider_call_id=provider_call_id,
                    provider_event_id=provider_event_id,
                )
                if existing:
                    if not bool(existing.get("replay_state_live")):
                        return self._deny(
                            provider,
                            "stale_provider_replay",
                            call_id=str(existing["id"]),
                        )
                    decision = self._decision_from_call(existing, replay=True)
                    self._metric(provider, "duplicate_replay")
                    return decision

                controls = await conn.fetchrow(
                    """
                    SELECT inbound_enabled, inbound_recording_enabled,
                           inbound_transfer_enabled, inbound_settlement_enabled,
                           inbound_controls_version
                    FROM platform_runtime_controls WHERE id=1
                    """
                )
                if not controls or not controls["inbound_enabled"]:
                    return self._deny(provider, "global_inbound_disabled")

                bindings = list(
                    await conn.fetch(
                        """
                        SELECT
                            a.id AS assignment_id,
                            a.tenant_id,
                            a.phone_number_id,
                            a.campaign_id,
                            a.config_id,
                            a.sip_trunk_id,
                            st.trunk_name AS sip_trunk_name,
                            a.canonical_did,
                            a.version AS route_version,
                            a.status AS assignment_status,
                            cfg.version AS config_version,
                            cfg.status AS config_status,
                            cfg.name AS inbound_name,
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
                            c.prompt_version_pin,
                            c.knowledge_mode,
                            c.knowledge_model,
                            pn.status AS phone_status,
                            st.is_active AS trunk_active,
                            st.direction AS trunk_direction,
                            st.metadata AS trunk_metadata,
                            st.live_registration_status AS trunk_live_registration_status,
                            st.live_status_detail AS trunk_live_status_detail,
                            st.live_status_checked_at AS trunk_live_status_checked_at,
                            t.status AS tenant_status,
                            t.subscription_status,
                            t.minutes_allocated,
                            COALESCE(tic.inbound_enabled, FALSE) AS tenant_inbound_enabled,
                            EXISTS (
                                SELECT 1
                                FROM tenant_telephony_concurrency_policies tcp
                                WHERE tcp.tenant_id=a.tenant_id AND tcp.is_active=TRUE
                            ) AS concurrency_policy_ready,
                            ai.id AS tenant_ai_config_id,
                            ai.updated_at AS tenant_ai_config_updated_at,
                            ai.llm_provider AS tenant_llm_provider,
                            ai.llm_model AS tenant_llm_model,
                            ai.llm_temperature AS tenant_llm_temperature,
                            ai.llm_max_tokens AS tenant_llm_max_tokens,
                            ai.stt_provider AS tenant_stt_provider,
                            ai.stt_model AS tenant_stt_model,
                            ai.stt_engine AS tenant_stt_engine,
                            ai.stt_language AS tenant_stt_language,
                            ai.tts_provider AS tenant_tts_provider,
                            ai.tts_model AS tenant_tts_model,
                            ai.tts_voice_id AS tenant_tts_voice_id,
                            ai.tts_sample_rate AS tenant_tts_sample_rate,
                            ai.voice_tuning AS tenant_voice_tuning,
                            ai.pipeline_mode AS tenant_pipeline_mode,
                            ai.realtime_model AS tenant_realtime_model,
                            ai.realtime_voice AS tenant_realtime_voice,
                            ai.realtime_settings AS tenant_realtime_settings
                        FROM inbound_did_assignments a
                        JOIN inbound_campaign_configs cfg
                          ON cfg.id=a.config_id AND cfg.tenant_id=a.tenant_id
                        JOIN campaigns c
                          ON c.id=a.campaign_id AND c.tenant_id=a.tenant_id
                        JOIN tenant_phone_numbers pn
                          ON pn.id=a.phone_number_id AND pn.tenant_id=a.tenant_id
                        JOIN tenant_sip_trunks st
                          ON st.id=a.sip_trunk_id AND st.tenant_id=a.tenant_id
                        JOIN tenants t ON t.id=a.tenant_id
                        LEFT JOIN tenant_inbound_controls tic ON tic.tenant_id=a.tenant_id
                        LEFT JOIN tenant_ai_configs ai ON ai.tenant_id=a.tenant_id
                        WHERE a.canonical_did=$1
                          AND a.status='active'
                          AND a.valid_from <= NOW()
                          AND (a.valid_to IS NULL OR a.valid_to > NOW())
                        ORDER BY a.id
                        LIMIT 2
                        """,
                        did,
                    )
                )
                if not bindings:
                    return self._deny(provider, "unknown_did")
                if len(bindings) != 1:
                    logger.critical(
                        "inbound_admission_ambiguous did_ref=%s matches=%d",
                        redact_did(did),
                        len(bindings),
                    )
                    return self._deny(provider, "ambiguous_did")
                route = dict(bindings[0])
                tenant_id = str(route["tenant_id"])
                if context_tenant and context_tenant != tenant_id:
                    return self._deny(provider, "tenant_conflict")
                trunk_runtime = evaluate_trunk_runtime(route)
                eligibility = (
                    (bool(route["tenant_inbound_enabled"]), "tenant_inbound_disabled"),
                    (route["tenant_status"] == "active", "tenant_inactive"),
                    (
                        route["subscription_status"] in {"active", "trialing"},
                        "subscription_inactive",
                    ),
                    (route["campaign_direction"] == "inbound", "campaign_not_inbound"),
                    (
                        is_active_inbound_campaign_status(route["campaign_status"]),
                        "base_campaign_inactive",
                    ),
                    (route["config_status"] == "active", "campaign_inactive"),
                    (route["phone_status"] == "verified", "did_not_verified"),
                    (trunk_runtime.ready, "trunk_not_ready"),
                    (
                        bool(route["concurrency_policy_ready"]),
                        "concurrency_policy_missing",
                    ),
                    (bool(route["tenant_ai_config_id"]), "ai_config_missing"),
                    (
                        all(
                            str(route.get(field) or "").strip()
                            for field in (
                                "tenant_llm_provider",
                                "tenant_llm_model",
                                "tenant_stt_provider",
                                "tenant_stt_model",
                                "tenant_tts_provider",
                                "tenant_tts_model",
                            )
                        ),
                        "ai_config_incomplete",
                    ),
                    (
                        str(route.get("tenant_pipeline_mode") or "cascaded")
                        in {"cascaded", "realtime"},
                        "ai_pipeline_invalid",
                    ),
                )
                for passed, reason in eligibility:
                    if not passed:
                        return self._deny(provider, reason)

                if bool(route["recording_enabled"]):
                    consent_text = str(route.get("consent_message") or "").strip()
                    if not consent_text:
                        return self._deny(provider, "missing_recording_consent")
                    from app.domain.services.recording_policy_service import (
                        contains_unsupported_dtmf_opt_out,
                    )

                    if contains_unsupported_dtmf_opt_out(consent_text):
                        # Defence in depth for legacy/administratively written
                        # configs that bypassed API validation: never pin or
                        # speak a promise that the media stack cannot honour.
                        return self._deny(
                            provider,
                            "unsupported_recording_dtmf_opt_out",
                        )

                schedule_decision = evaluate_business_hours(
                    str(route["timezone"] or ""),
                    _json_obj(route["business_hours"]),
                )
                if not schedule_decision.valid:
                    return self._deny(provider, schedule_decision.reason)
                selected_action = (
                    str(route["after_hours_action"])
                    if schedule_decision.is_after_hours
                    else "agent"
                )
                if selected_action not in {"agent", "hangup", "voicemail", "transfer"}:
                    return self._deny(provider, "invalid_after_hours_action")
                selected_destination: Optional[str] = None
                transfer_policy = _json_obj(route["transfer_policy"])
                transfer_requested = (
                    selected_action == "transfer" or transfer_policy.get("enabled") is True
                )
                if (
                    transfer_requested
                    and str(route.get("trunk_direction") or "").strip().lower() != "both"
                ):
                    return self._deny(provider, "transfer_trunk_not_bidirectional")
                configured_max_duration = transfer_policy.get(
                    "max_call_duration_seconds",
                    1_800,
                )
                if (
                    isinstance(configured_max_duration, bool)
                    or not isinstance(configured_max_duration, int)
                    or not 60 <= configured_max_duration <= 14_400
                ):
                    return self._deny(provider, "invalid_max_call_duration")
                if selected_action == "transfer":
                    # A transfer may only be admitted when the supervised
                    # linked-leg runtime and the operator-controlled live
                    # switch are both enabled. The route snapshot below pins
                    # the exact destination and hard deadline before Answer.
                    if transfer_policy.get("enabled") is not True:
                        return self._deny(provider, "transfer_policy_disabled")
                    selected_destination = normalize_did(route["transfer_number"])
                    if not selected_destination:
                        return self._deny(provider, "invalid_transfer_destination")
                    if not inbound_transfer_destination_approved(
                        transfer_policy,
                        selected_destination,
                    ):
                        return self._deny(provider, "transfer_destination_not_approved")
                    if not inbound_transfer_runtime_available():
                        return self._deny(provider, "transfer_runtime_unavailable")
                    if not inbound_transfer_scope_available(
                        tenant_id=tenant_id,
                        config_id=route.get("config_id"),
                    ):
                        return self._deny(provider, "transfer_staging_scope_mismatch")
                    if not controls["inbound_transfer_enabled"]:
                        return self._deny(provider, "transfer_disabled")
                business_hours = _json_obj(route["business_hours"])
                after_hours_message = business_hours.get("after_hours_message")
                if after_hours_message is not None and not isinstance(after_hours_message, str):
                    return self._deny(provider, "invalid_after_hours_message")
                if isinstance(after_hours_message, str):
                    after_hours_message = after_hours_message.strip() or None
                if selected_action == "voicemail" and not after_hours_message:
                    # This product action is conversational AI message intake,
                    # not a beep/one-way voicemail recorder. A caller must hear
                    # an explicit pinned instruction before the agent collects
                    # their message; the generic runtime fallback is not an
                    # activatable policy contract.
                    return self._deny(
                        provider,
                        "voicemail_intake_message_required",
                    )

                # Resolve code/env defaults and the already-loaded tenant DB
                # partial while the call is still in its pre-answer admission
                # transaction.  The resulting complete tuning object is what is
                # pinned to the durable route snapshot; runtime never has to
                # re-read mutable tenant configuration after Answer.
                from app.domain.services.voice_tuning import (
                    get_voice_tuning_resolver,
                )

                tuning_resolver = get_voice_tuning_resolver()
                effective_voice_tuning = asdict(tuning_resolver.for_tenant(tenant_id))
                effective_voice_tuning.update(
                    tuning_resolver.coerce_user_partial(_json_obj(route["tenant_voice_tuning"]))
                )

                from app.services.scripts.knowledge.retrieval import knowledge_enabled

                pinned_knowledge_enabled = knowledge_enabled()
                pinned_knowledge_mode = str(route.get("knowledge_mode") or "none").strip().lower()
                knowledge_nodes: list[dict[str, Any]] = []
                if pinned_knowledge_enabled and pinned_knowledge_mode in {
                    "inline",
                    "map_retrieve",
                    "retrieve",
                }:
                    rows = await conn.fetch(
                        """
                        SELECT id, depth, path, position, heading, content,
                               summary, voice_answer, keywords,
                               example_questions, search_text, priority,
                               updated_at
                        FROM campaign_knowledge_nodes
                        WHERE campaign_id=$1::uuid AND tenant_id=$2::uuid
                          AND enabled
                        ORDER BY string_to_array(path, '.')::int[]
                        """,
                        route["campaign_id"],
                        route["tenant_id"],
                    )
                    for raw_node in rows:
                        node = dict(raw_node)
                        for key in ("id", "updated_at"):
                            if node.get(key) is not None:
                                node[key] = str(node[key])
                        for key in ("keywords", "example_questions"):
                            if node.get(key) is not None:
                                node[key] = list(node[key])
                        knowledge_nodes.append(node)
                knowledge_checksum = hashlib.sha256(
                    json.dumps(
                        knowledge_nodes,
                        sort_keys=True,
                        separators=(",", ":"),
                        default=str,
                    ).encode("utf-8")
                ).hexdigest()

                snapshot: dict[str, Any] = {
                    "campaign": {
                        "id": str(route["campaign_id"]),
                        "tenant_id": tenant_id,
                        "name": route["campaign_name"],
                        "description": route["campaign_description"],
                        "status": route["campaign_status"],
                        "direction": route["campaign_direction"],
                        "system_prompt": route["system_prompt"],
                        "voice_id": route["voice_id"],
                        "tts_provider": route["tts_provider"],
                        "goal": route["goal"],
                        "script_config": _json_obj(route["script_config"]),
                        "calling_config": _json_obj(route["calling_config"]),
                        "prompt_version_pin": route["prompt_version_pin"],
                        "knowledge_mode": route["knowledge_mode"],
                        "knowledge_model": route["knowledge_model"],
                    },
                    "inbound_config": {
                        "id": str(route["config_id"]),
                        "name": route["inbound_name"],
                        "version": int(route["config_version"]),
                        "checksum": route["config_checksum"],
                        "opening_mode": route["opening_mode"],
                        "greeting": route["greeting"],
                        "timezone": route["timezone"],
                        "business_hours": business_hours,
                        "after_hours_action": route["after_hours_action"],
                        "after_hours_message": after_hours_message,
                        "is_after_hours": schedule_decision.is_after_hours,
                        "selected_action": selected_action,
                        "selected_destination": selected_destination,
                        "transfer_number": route["transfer_number"],
                        "recording_enabled": bool(route["recording_enabled"]),
                        "consent_message": route["consent_message"],
                        "recording_policy": _json_obj(route["recording_policy"]),
                        "transfer_policy": transfer_policy,
                        "qualification_config": _json_obj(route["qualification_config"]),
                    },
                    "route": {
                        "assignment_id": str(route["assignment_id"]),
                        "config_id": str(route["config_id"]),
                        "phone_number_id": str(route["phone_number_id"]),
                        "sip_trunk_id": str(route["sip_trunk_id"]),
                        "sip_trunk_name": str(route.get("sip_trunk_name") or ""),
                        "called_did": did,
                        "route_version": int(route["route_version"]),
                        "config_version": int(route["config_version"]),
                        "ingress": ingress,
                    },
                    "controls": {
                        "platform_version": int(controls["inbound_controls_version"]),
                        "recording_enabled": bool(controls["inbound_recording_enabled"]),
                        "transfer_enabled": bool(controls["inbound_transfer_enabled"]),
                        "settlement_enabled": bool(controls["inbound_settlement_enabled"]),
                    },
                    "schedule_decision": {
                        **schedule_decision.to_dict(),
                        "selected_action": selected_action,
                        "selected_destination": selected_destination,
                        "after_hours_message": after_hours_message,
                    },
                    "tenant_ai_config_id": (
                        str(route["tenant_ai_config_id"]) if route["tenant_ai_config_id"] else None
                    ),
                    "tenant_ai_config_updated_at": (
                        str(route["tenant_ai_config_updated_at"])
                        if route["tenant_ai_config_updated_at"]
                        else None
                    ),
                    "tenant_ai_config": {
                        "id": (
                            str(route["tenant_ai_config_id"])
                            if route["tenant_ai_config_id"]
                            else None
                        ),
                        "updated_at": (
                            str(route["tenant_ai_config_updated_at"])
                            if route["tenant_ai_config_updated_at"]
                            else None
                        ),
                        "llm_provider": route["tenant_llm_provider"],
                        "llm_model": route["tenant_llm_model"],
                        "llm_temperature": route["tenant_llm_temperature"],
                        "llm_max_tokens": route["tenant_llm_max_tokens"],
                        "stt_provider": route["tenant_stt_provider"],
                        "stt_model": route["tenant_stt_model"],
                        "stt_engine": route["tenant_stt_engine"] or "deepgram_flux",
                        "stt_language": route["tenant_stt_language"],
                        "tts_provider": route["tenant_tts_provider"],
                        "tts_model": route["tenant_tts_model"],
                        "tts_voice_id": route["tenant_tts_voice_id"],
                        "tts_sample_rate": route["tenant_tts_sample_rate"],
                        "voice_tuning": effective_voice_tuning,
                        "pipeline_mode": route["tenant_pipeline_mode"] or "cascaded",
                        "realtime_model": route["tenant_realtime_model"] or "gpt-realtime-2",
                        "realtime_voice": route["tenant_realtime_voice"] or "marin",
                        "realtime_settings": _json_obj(route["tenant_realtime_settings"]),
                    },
                    "knowledge_snapshot": {
                        "enabled": pinned_knowledge_enabled,
                        "mode": pinned_knowledge_mode,
                        "model": route.get("knowledge_model"),
                        "tenant_id": tenant_id,
                        "campaign_id": str(route["campaign_id"]),
                        "checksum": knowledge_checksum,
                        "node_count": len(knowledge_nodes),
                        "nodes": knowledge_nodes,
                    },
                }
                call_uuid = str(uuid.uuid4())
                talklee_call_id = "IN" + uuid.uuid4().hex[:18]
                try:
                    inserted = await conn.fetchrow(
                        """
                        INSERT INTO calls (
                            id, tenant_id, campaign_id, lead_id, phone_number,
                            external_call_uuid, status, started_at, talklee_call_id,
                            direction, provider, provider_call_id, provider_event_id,
                            called_did, called_did_id, assignment_id, ingress,
                            route_version, config_version, route_snapshot,
                            admission_status, admission_reason, caller_ani,
                            caller_ani_private, consent_status, processing_status,
                            billing_status, reserved_seconds
                        ) VALUES (
                            $1,$2,$3,NULL,$4,$5,'initiated',NOW(),$6,
                            'inbound',$7,$8,$9,$10,$11,$12,$13,$14,$15,$16::jsonb,
                            'pending','evaluating',$17,$18,
                            CASE WHEN $19 THEN 'pending' ELSE 'not_required' END,
                            'pending','none',0
                        )
                        ON CONFLICT (provider, provider_call_id)
                            WHERE provider IS NOT NULL AND provider_call_id IS NOT NULL
                        DO NOTHING
                        RETURNING *
                        """,
                        call_uuid,
                        tenant_id,
                        route["campaign_id"],
                        caller_ani or "anonymous",
                        provider_call_id[:100],
                        talklee_call_id,
                        provider,
                        provider_call_id,
                        provider_event_id,
                        did,
                        route["phone_number_id"],
                        route["assignment_id"],
                        ingress,
                        route["route_version"],
                        route["config_version"],
                        json.dumps(snapshot, default=str),
                        caller_ani,
                        caller_private,
                        bool(route["recording_enabled"]),
                    )
                except asyncpg.UniqueViolationError:
                    inserted = None
                if not inserted:
                    existing = await self._existing_call(
                        conn,
                        provider=provider,
                        provider_call_id=provider_call_id,
                        provider_event_id=provider_event_id,
                    )
                    if existing:
                        if not bool(existing.get("replay_state_live")):
                            return self._deny(
                                provider,
                                "stale_provider_replay",
                                call_id=str(existing["id"]),
                            )
                        decision = self._decision_from_call(existing, replay=True)
                        self._metric(provider, "duplicate_replay")
                        return decision
                    raise RuntimeError("provider identity conflict without retrievable call")

                lease = await self._limiter.acquire_lease(
                    conn,
                    tenant_id=tenant_id,
                    call_id=call_uuid,
                    talklee_call_id=talklee_call_id,
                    lease_kind=LeaseKind.CALL,
                    request_id=request.request_id,
                    metadata={
                        "direction": "inbound",
                        "assignment_id": str(route["assignment_id"]),
                    },
                )
                if not lease.accepted:
                    await conn.execute(
                        """
                        UPDATE calls
                        SET admission_status='denied', admission_reason=$2,
                            processing_status='pending',
                            status='termination_pending',
                            ended_at=NULL, updated_at=NOW()
                        WHERE id=$1
                        """,
                        call_uuid,
                        lease.reason,
                    )
                    return self._deny(
                        provider,
                        lease.reason,
                        call_id=call_uuid,
                        talklee_call_id=talklee_call_id,
                        tenant_id=tenant_id,
                        campaign_id=str(route["campaign_id"]),
                        config_id=str(route["config_id"]),
                        assignment_id=str(route["assignment_id"]),
                        trunk_id=str(route["sip_trunk_id"]),
                        route_version=int(route["route_version"]),
                        config_version=int(route["config_version"]),
                        opening_mode=route["opening_mode"],
                        config_snapshot=snapshot,
                    )

                # The limiter's tenant advisory lock remains held for this
                # transaction, so active-reservation counting is race-free.
                quota = await conn.fetchrow(
                    "SELECT minutes_allocated FROM tenants WHERE id=$1 FOR UPDATE",
                    tenant_id,
                )
                allocated_minutes = int(quota["minutes_allocated"] or 0)
                # Use the product-wide quota authority: this month's actual
                # duration from *all* call directions.  Add only currently
                # active inbound reservations, because their duration has not
                # been written yet.  The immutable ledger remains the billing
                # audit trail, but it must not drift from dashboard/quota
                # accounting. A non-positive allocation is the established
                # unlimited-plan sentinel.
                accounted_seconds = await fetch_tenant_accounted_usage_seconds(
                    conn,
                    tenant_id=tenant_id,
                    exclude_call_id=call_uuid,
                )
                # Reserve the entire enforceable call window before Answer.
                # ``request.reservation_seconds`` is a caller-side safety cap;
                # the live Asterisk callback passes 14,400 so the campaign's
                # pinned policy wins, while tests/manual callers may request a
                # smaller cap. Limited plans are shortened to their exact
                # remaining seconds and rejected when less than one minute is
                # available. The runtime timer uses this same pinned value.
                reservation_seconds = min(
                    configured_max_duration,
                    requested_reservation_seconds,
                )
                if allocated_minutes > 0:
                    remaining_seconds = max(
                        0,
                        allocated_minutes * 60 - accounted_seconds,
                    )
                    reservation_seconds = min(
                        reservation_seconds,
                        remaining_seconds,
                    )
                if reservation_seconds < 60:
                    await conn.execute(
                        """
                        UPDATE calls
                        SET admission_status='denied', admission_reason='insufficient_minutes',
                            processing_status='pending', status='termination_pending',
                            concurrency_lease_id=$2, ended_at=NULL,
                            updated_at=NOW()
                        WHERE id=$1
                        """,
                        call_uuid,
                        lease.lease_id,
                    )
                    return self._deny(
                        provider,
                        "insufficient_minutes",
                        call_id=call_uuid,
                        talklee_call_id=talklee_call_id,
                        tenant_id=tenant_id,
                        campaign_id=str(route["campaign_id"]),
                        config_id=str(route["config_id"]),
                        assignment_id=str(route["assignment_id"]),
                        trunk_id=str(route["sip_trunk_id"]),
                        route_version=int(route["route_version"]),
                        config_version=int(route["config_version"]),
                        opening_mode=route["opening_mode"],
                        config_snapshot=snapshot,
                        concurrency_lease_id=str(lease.lease_id),
                    )
                snapshot["route"]["max_call_duration_seconds"] = reservation_seconds
                snapshot["route"]["reservation_seconds"] = reservation_seconds
                reservation_key = f"inbound:reserve:{provider}:{provider_call_id}"
                reservation = await conn.fetchrow(
                    """
                    INSERT INTO inbound_usage_transactions (
                        tenant_id, call_id, call_leg_id, transaction_type,
                        quantity_seconds, idempotency_key, policy_snapshot,
                        metadata
                    ) VALUES ($1,$2,NULL,'reserve',$3,$4,$5::jsonb,$6::jsonb)
                    ON CONFLICT (tenant_id, idempotency_key) DO NOTHING
                    RETURNING id
                    """,
                    tenant_id,
                    call_uuid,
                    reservation_seconds,
                    reservation_key,
                    json.dumps(
                        {
                            "allocated_minutes": allocated_minutes,
                            "accounted_seconds_at_reservation": accounted_seconds,
                            "reservation_seconds": reservation_seconds,
                        }
                    ),
                    json.dumps({"provider": provider, "request_id": request.request_id}),
                )
                if not reservation:
                    raise RuntimeError("failed to create usage reservation")
                snapshot["route"]["usage_reservation_id"] = str(reservation["id"])
                await conn.execute(
                    """
                    UPDATE calls
                    SET admission_status='allowed', admission_reason='allowed',
                        processing_status='active', billing_status='reserved',
                        reserved_seconds=$2, concurrency_lease_id=$3,
                        route_snapshot=$4::jsonb, updated_at=NOW()
                    WHERE id=$1
                    """,
                    call_uuid,
                    reservation_seconds,
                    lease.lease_id,
                    json.dumps(snapshot, default=str),
                )
                decision = InboundAdmissionDecision(
                    allowed=True,
                    reason="allowed",
                    call_id=call_uuid,
                    talklee_call_id=talklee_call_id,
                    tenant_id=tenant_id,
                    campaign_id=str(route["campaign_id"]),
                    config_id=str(route["config_id"]),
                    assignment_id=str(route["assignment_id"]),
                    trunk_id=str(route["sip_trunk_id"]),
                    route_version=int(route["route_version"]),
                    config_version=int(route["config_version"]),
                    opening_mode=route["opening_mode"],
                    config_snapshot=snapshot,
                    concurrency_lease_id=str(lease.lease_id),
                    usage_reservation_id=str(reservation["id"]),
                )
            self._usage_metric("reserve", "inserted")
            self._metric(provider, "allowed")
            logger.info(
                "inbound_admission_allowed call=%s tenant=%s did_ref=%s "
                "route_version=%s config_version=%s",
                call_uuid[:8],
                tenant_id[:8],
                redact_did(did),
                route["route_version"],
                route["config_version"],
            )
            return decision
        except Exception as exc:  # fail closed; no partial transaction commits
            logger.error(
                "inbound_admission_dependency_failure provider=%s did_ref=%s err_type=%s",
                provider,
                redact_did(did),
                type(exc).__name__,
                exc_info=True,
            )
            return self._deny(provider, "admission_dependency_unavailable")

    async def finalize(
        self,
        request: InboundFinalizationRequest,
        *,
        release_only: bool = False,
    ) -> InboundFinalizationResult:
        provider = _provider(request.provider)
        provider_call_id = _call_id(request.provider_call_id)
        try:
            call_id = str(uuid.UUID(str(request.call_id)))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("call_id must be a UUID") from exc
        if not provider or not provider_call_id:
            raise ValueError("provider and provider_call_id are required")
        terminal_status = str(request.terminal_status or "").strip().lower()
        if terminal_status not in _TERMINAL_STATUSES:
            raise ValueError(f"Unsupported terminal_status: {terminal_status}")
        duration = max(0, int(request.duration_seconds))
        cost = Decimal(str(request.cost)) if request.cost is not None else None
        if cost is not None and (not cost.is_finite() or cost < 0):
            raise ValueError("cost must be a finite, non-negative amount")
        outcome = str(request.outcome or "").strip().lower() or None
        if outcome is not None and not _OUTCOME_RE.fullmatch(outcome):
            raise ValueError("outcome must be a stable lowercase identifier")

        async with acquire_with_tenant(self._pool, None) as conn:
            call = await conn.fetchrow(
                """
                SELECT * FROM calls
                WHERE id=$1 AND direction='inbound' AND provider=$2
                  AND provider_call_id=$3
                FOR UPDATE
                """,
                call_id,
                provider,
                provider_call_id,
            )
            if not call:
                raise LookupError("Inbound call not found for provider identity")
            unsettled_transfer_usage = await conn.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM call_legs
                    WHERE call_id=$1::uuid AND leg_type='transfer'
                      AND (
                          status IN ('initiated','ringing','answered')
                          OR billing_status NOT IN (
                              'finalized','released','reversed'
                          )
                      )
                      AND NOT (
                          status='reconciliation_required'
                          AND billing_status='held'
                          AND LOWER(COALESCE(
                              metadata->>'restart_answer_state_ambiguous',''
                          ))='true'
                      )
                )
                """,
                call_id,
            )
            if unsettled_transfer_usage is True:
                # The inbound parent and transfer target are independently
                # billable PSTN subjects. A parent terminal transaction cannot
                # become durable while a child reservation is still live.
                # Restart-ambiguous children are the sole exception: their PBX
                # absence and released concurrency lease are proven, while the
                # exact reservation remains deliberately held for carrier-CDR
                # reconciliation.
                raise RuntimeError("inbound_transfer_usage_nonterminal")
            # The provider endpoint can commit a more precise terminal state
            # (for example ``ended``) before restart recovery settles the
            # still-reserved billing row. The row is locked here, so preserve
            # the canonical first terminal writer while continuing every
            # usage/lease operation below. Settlement must never rewrite
            # terminal history merely because recovery inferred ``completed``.
            current_status = str(call.get("status") or "").strip().lower()
            status_to_persist = (
                current_status if current_status in TERMINAL_CALL_STATUSES else terminal_status
            )
            if call["billing_status"] in {"finalized", "released", "reversed"}:
                # A retry may be the first callback carrying the resolved
                # conversational outcome (for example after a crash between
                # billing commit and projection). Repair that nullable
                # projection idempotently without touching the immutable
                # settlement ledger.
                if not release_only and outcome and not call.get("outcome"):
                    await conn.execute(
                        "UPDATE calls SET outcome=$2, updated_at=NOW() "
                        "WHERE id=$1 AND outcome IS NULL",
                        call_id,
                        outcome,
                    )
                terminal = await conn.fetchrow(
                    """
                    SELECT id, transaction_type FROM inbound_usage_transactions
                    WHERE call_id=$1 AND call_leg_id IS NULL
                      AND transaction_type IN ('finalize','release','reverse')
                    ORDER BY created_at LIMIT 1
                    """,
                    call_id,
                )
                return InboundFinalizationResult(
                    finalized=True,
                    reason="duplicate_replay",
                    call_id=call_id,
                    billing_status=call["billing_status"],
                    lease_released=True,
                    usage_transaction_id=str(terminal["id"]) if terminal else None,
                    is_replay=True,
                )
            if (
                call["billing_status"] == "held"
                and call.get("billing_hold_reason") == _ANSWER_AMBIGUOUS_HOLD_REASON
            ):
                # Only authoritative carrier/CDR reconciliation may resolve
                # whether a process killed across ARI Answer became billable.
                # Duplicate provider/watchdog callbacks cannot guess it into
                # an automatic charge or a zero-second release.
                return InboundFinalizationResult(
                    finalized=False,
                    reason=_ANSWER_AMBIGUOUS_HOLD_REASON,
                    call_id=call_id,
                    billing_status="held",
                    lease_released=True,
                    is_replay=True,
                )
            if call["billing_status"] == "held":
                # A held row already contains the first terminal writer's
                # billable facts. Later/reordered callbacks may retry the
                # transition, but may not lower duration or replace cost or
                # outcome before automatic settlement is re-enabled.
                duration = max(0, int(call.get("duration_seconds") or 0))
                persisted_cost = call.get("cost")
                cost = Decimal(str(persisted_cost)) if persisted_cost is not None else None
                persisted_outcome = str(call.get("outcome") or "").strip().lower()
                outcome = persisted_outcome or None
            if not release_only and request.reason == _ANSWER_AMBIGUOUS_TERMINAL_REASON:
                lease_released = True
                if call["concurrency_lease_id"]:
                    lease_released = (
                        bool(
                            await self._limiter.release_lease(
                                conn,
                                tenant_id=str(call["tenant_id"]),
                                lease_id=call["concurrency_lease_id"],
                                reason=_ANSWER_AMBIGUOUS_HOLD_REASON,
                                request_id=request.request_id,
                            )
                        )
                        or call["billing_status"] == "held"
                    )
                processing_status = (
                    "completed" if status_to_persist in {"completed", "ended"} else "failed"
                )
                await conn.execute(
                    """
                    UPDATE calls
                    SET status=$2, duration_seconds=$3,
                        cost=COALESCE($4,cost), outcome=COALESCE($5,outcome),
                        ended_at=COALESCE(ended_at,NOW()),
                        processing_status=$6, billing_status='held',
                        billing_hold_reason='provider_answer_ambiguous',
                        updated_at=NOW()
                    WHERE id=$1
                    """,
                    call_id,
                    status_to_persist,
                    duration,
                    cost,
                    outcome,
                    processing_status,
                )
                self._usage_metric("finalize", "provider_answer_ambiguous")
                return InboundFinalizationResult(
                    finalized=False,
                    reason=_ANSWER_AMBIGUOUS_HOLD_REASON,
                    call_id=call_id,
                    billing_status="held",
                    lease_released=lease_released,
                    is_replay=call["billing_status"] == "held",
                )
            if not release_only:
                settlement_controls = await conn.fetchrow(
                    """
                    SELECT inbound_settlement_enabled
                    FROM platform_runtime_controls WHERE id=1
                    """
                )
                if not settlement_controls or not settlement_controls["inbound_settlement_enabled"]:
                    lease_released = True
                    if call["concurrency_lease_id"]:
                        lease_released = (
                            bool(
                                await self._limiter.release_lease(
                                    conn,
                                    tenant_id=str(call["tenant_id"]),
                                    lease_id=call["concurrency_lease_id"],
                                    reason="settlement_held",
                                    request_id=request.request_id,
                                )
                            )
                            or call["billing_status"] == "held"
                        )
                    processing_status = (
                        "completed" if status_to_persist in {"completed", "ended"} else "failed"
                    )
                    await conn.execute(
                        """
                        UPDATE calls
                        SET status=$2, duration_seconds=$3,
                            cost=COALESCE($4,cost), outcome=COALESCE($5,outcome),
                            ended_at=COALESCE(ended_at,NOW()),
                            processing_status=$6, billing_status='held',
                            billing_hold_reason=CASE
                                WHEN billing_hold_reason=
                                     'usage_exceeded_reservation'
                                    THEN billing_hold_reason
                                ELSE 'settlement_switch_disabled'
                            END,
                            updated_at=NOW()
                        WHERE id=$1
                        """,
                        call_id,
                        status_to_persist,
                        duration,
                        cost,
                        outcome,
                        processing_status,
                    )
                    self._usage_metric("finalize", "settlement_switch_disabled")
                    return InboundFinalizationResult(
                        finalized=False,
                        reason="settlement_held",
                        call_id=call_id,
                        billing_status="held",
                        lease_released=lease_released,
                        is_replay=call["billing_status"] == "held",
                    )
            reserve = await conn.fetchrow(
                """
                SELECT id, quantity_seconds FROM inbound_usage_transactions
                WHERE call_id=$1 AND call_leg_id IS NULL
                  AND transaction_type='reserve'
                """,
                call_id,
            )
            reserved = int(
                reserve["quantity_seconds"] if reserve else call["reserved_seconds"] or 0
            )
            if not release_only and duration > reserved:
                # Runtime is required to hang up at the same duration that was
                # reserved before Answer. If provider timing or corrupted
                # state reports a larger value, never silently charge beyond
                # quota: hold the settlement for explicit reconciliation.
                lease_released = True
                if call["concurrency_lease_id"]:
                    lease_released = (
                        bool(
                            await self._limiter.release_lease(
                                conn,
                                tenant_id=str(call["tenant_id"]),
                                lease_id=call["concurrency_lease_id"],
                                reason="usage_exceeded_reservation",
                                request_id=request.request_id,
                            )
                        )
                        or call["billing_status"] == "held"
                    )
                processing_status = (
                    "completed" if status_to_persist in {"completed", "ended"} else "failed"
                )
                await conn.execute(
                    """
                        UPDATE calls
                        SET status=$2, duration_seconds=$3,
                            cost=COALESCE($4,cost), outcome=COALESCE($5,outcome),
                            ended_at=COALESCE(ended_at,NOW()),
                            processing_status=$6, billing_status='held',
                            billing_hold_reason='usage_exceeded_reservation',
                            updated_at=NOW()
                        WHERE id=$1
                    """,
                    call_id,
                    status_to_persist,
                    duration,
                    cost,
                    outcome,
                    processing_status,
                )
                self._usage_metric("finalize", "usage_exceeded_reservation")
                return InboundFinalizationResult(
                    finalized=False,
                    reason="usage_exceeded_reservation",
                    call_id=call_id,
                    billing_status="held",
                    lease_released=lease_released,
                    is_replay=call["billing_status"] == "held",
                )
            transaction_type = "release" if release_only else "finalize"
            delta = -reserved if release_only else duration - reserved
            idempotency_key = f"inbound:{transaction_type}:{provider}:{provider_call_id}"
            usage = await conn.fetchrow(
                """
                INSERT INTO inbound_usage_transactions (
                    tenant_id, call_id, call_leg_id, transaction_type,
                    quantity_seconds, amount, idempotency_key,
                    related_transaction_id, policy_snapshot, metadata
                ) VALUES ($1,$2,NULL,$3,$4,$5,$6,$7,$8::jsonb,$9::jsonb)
                ON CONFLICT (tenant_id, idempotency_key) DO NOTHING
                RETURNING id
                """,
                call["tenant_id"],
                call_id,
                transaction_type,
                delta,
                cost,
                idempotency_key,
                reserve["id"] if reserve else None,
                json.dumps(
                    {
                        "reserved_seconds": reserved,
                        "actual_seconds": 0 if release_only else duration,
                    }
                ),
                json.dumps({"reason": request.reason, "request_id": request.request_id}),
            )
            if not usage:
                usage = await conn.fetchrow(
                    """
                    SELECT id FROM inbound_usage_transactions
                    WHERE tenant_id=$1 AND idempotency_key=$2
                    """,
                    call["tenant_id"],
                    idempotency_key,
                )
            lease_released = True
            if call["concurrency_lease_id"]:
                lease_released = await self._limiter.release_lease(
                    conn,
                    tenant_id=str(call["tenant_id"]),
                    lease_id=call["concurrency_lease_id"],
                    reason=(request.reason or transaction_type)[:64],
                    request_id=request.request_id,
                )
                if not lease_released:
                    # Already released is idempotently successful.
                    lease_released = True
            billing_status = "released" if release_only else "finalized"
            processing_status = (
                "released"
                if release_only
                else ("completed" if status_to_persist in {"completed", "ended"} else "failed")
            )
            await conn.execute(
                """
                UPDATE calls
                SET status=$2, duration_seconds=$3, cost=COALESCE($4,cost),
                    outcome=COALESCE($5,outcome),
                    ended_at=COALESCE(ended_at,NOW()),
                    admission_status=CASE WHEN $6 THEN 'released' ELSE admission_status END,
                    admission_reason=CASE WHEN $6 THEN $7 ELSE admission_reason END,
                    processing_status=$8, billing_status=$9,
                    billing_hold_reason=NULL, updated_at=NOW()
                WHERE id=$1
                """,
                call_id,
                status_to_persist,
                0 if release_only else duration,
                cost,
                outcome,
                release_only,
                request.reason or "released_before_media",
                processing_status,
                billing_status,
            )
            result = InboundFinalizationResult(
                finalized=True,
                reason="released" if release_only else "finalized",
                call_id=call_id,
                billing_status=billing_status,
                lease_released=lease_released,
                usage_transaction_id=str(usage["id"]) if usage else None,
            )
        self._usage_metric(transaction_type, "inserted")
        return result

    async def release(
        self,
        *,
        call_id: str,
        provider: str,
        provider_call_id: str,
        reason: str,
        request_id: Optional[str] = None,
    ) -> InboundFinalizationResult:
        return await self.finalize(
            InboundFinalizationRequest(
                call_id=call_id,
                provider=provider,
                provider_call_id=provider_call_id,
                terminal_status="failed",
                duration_seconds=0,
                outcome="failed",
                reason=reason,
                request_id=request_id,
            ),
            release_only=True,
        )

    async def resolve_billing_hold(
        self,
        request: InboundHoldResolutionRequest,
    ) -> InboundHoldResolutionResult:
        """Resolve one manual inbound billing hold from external evidence.

        This is intentionally separate from ``finalize``: live callbacks may
        create a hold, but only platform-admin adjudication carrying pinned
        CDR/provider evidence can consume it.  A no-charge release needs one
        admin. A charge-bearing finalize first creates an immutable request and
        then needs a different admin's explicit approval. The tenant lock
        serializes quota projection with admission, while approval, row locks,
        settlement-switch read, ledger insert, call CAS, durable idempotency,
        and audit events share one transaction.
        """

        actor_role = str(request.actor_role or "").strip().lower()
        if actor_role not in {"platform_admin", "super_admin"}:
            raise PermissionError("platform_admin is required to resolve inbound billing holds")
        try:
            call_id = str(uuid.UUID(str(request.call_id)))
            tenant_id = str(uuid.UUID(str(request.tenant_id)))
            actor_id = str(uuid.UUID(str(request.actor_id)))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("call_id, tenant_id, and actor_id must be UUIDs") from exc

        hold_reason = str(request.hold_reason or "").strip().lower()
        evidence_type = str(request.evidence_type or "").strip().lower()
        expected_evidence = _RESOLVABLE_HOLD_EVIDENCE.get(hold_reason)
        if expected_evidence is None:
            raise ValueError("unsupported inbound billing hold reason")
        if evidence_type != expected_evidence:
            raise ValueError(f"{hold_reason} requires {expected_evidence} external evidence")
        evidence_reference = str(request.evidence_reference or "").strip()
        if not 3 <= len(evidence_reference) <= 255:
            raise ValueError("evidence_reference must be between 3 and 255 characters")
        evidence_sha256 = str(request.evidence_sha256 or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", evidence_sha256):
            raise ValueError("evidence_sha256 must be a lowercase SHA-256 digest")
        adjudication_reason = str(request.adjudication_reason or "").strip()
        if not 8 <= len(adjudication_reason) <= 1000:
            raise ValueError("adjudication_reason must be between 8 and 1000 characters")
        request_id = str(request.request_id or "").strip()
        if not 8 <= len(request_id) <= 255:
            raise ValueError("request_id must be between 8 and 255 characters")

        decision = str(request.decision or "").strip().lower()
        if decision not in {"release_unanswered", "finalize"}:
            raise ValueError("decision must be release_unanswered or finalize")
        raw_duration = request.authoritative_duration_seconds
        if isinstance(raw_duration, bool):
            raise ValueError("authoritative_duration_seconds must be a nonnegative integer")
        if decision == "finalize":
            try:
                duration = int(raw_duration) if raw_duration is not None else -1
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError(
                    "finalize requires authoritative_duration_seconds within PostgreSQL INTEGER"
                ) from exc
            if (
                raw_duration is None
                or duration != raw_duration
                or not 0 <= duration <= 2_147_483_647
            ):
                raise ValueError(
                    "finalize requires authoritative_duration_seconds within PostgreSQL INTEGER"
                )
        else:
            if raw_duration not in {None, 0}:
                raise ValueError("release_unanswered requires zero authoritative duration")
            duration = 0

        authoritative_cost: Optional[Decimal]
        if request.authoritative_cost is None:
            authoritative_cost = None
        else:
            try:
                raw_cost = Decimal(str(request.authoritative_cost))
            except (InvalidOperation, ValueError) as exc:
                raise ValueError("authoritative_cost must fit nonnegative DECIMAL(10,4)") from exc
            if not raw_cost.is_finite() or raw_cost < 0 or raw_cost > _CALL_COST_MAX:
                raise ValueError("authoritative_cost must fit nonnegative DECIMAL(10,4) exactly")
            try:
                authoritative_cost = raw_cost.quantize(_CALL_COST_QUANTUM)
            except InvalidOperation as exc:
                raise ValueError(
                    "authoritative_cost must fit nonnegative DECIMAL(10,4) exactly"
                ) from exc
            if authoritative_cost != raw_cost:
                raise ValueError("authoritative_cost must fit nonnegative DECIMAL(10,4) exactly")

        authoritative_currency = str(request.authoritative_currency or "").strip().upper()
        if authoritative_cost is None:
            if authoritative_currency:
                raise ValueError("authoritative_currency requires authoritative_cost")
            authoritative_currency = None
        elif authoritative_currency not in _ISO_4217_CODES:
            raise ValueError("authoritative_cost requires an assigned ISO-4217 currency")
        if decision == "release_unanswered" and authoritative_cost not in {None, Decimal("0")}:
            raise ValueError("release_unanswered cannot carry a positive authoritative_cost")

        approval_action = str(request.approval_action or "").strip().lower() or None
        approval_request_id: Optional[str] = None
        if decision == "release_unanswered":
            if approval_action is not None or request.approval_request_id is not None:
                raise ValueError("release_unanswered does not use finalize approval fields")
        else:
            # Backward-compatible fail-closed behavior: an old finalize caller
            # can only create a pending request.  It can never settle money
            # until a second caller explicitly chooses approve.
            approval_action = approval_action or "request"
            if approval_action not in {"request", "approve"}:
                raise ValueError("finalize approval_action must be request or approve")
            if approval_action == "approve":
                try:
                    approval_request_id = str(uuid.UUID(str(request.approval_request_id)))
                except (ValueError, TypeError, AttributeError) as exc:
                    raise ValueError("approve requires a UUID approval_request_id") from exc
            elif request.approval_request_id is not None:
                raise ValueError("approval_request_id is only valid when approving")

        transaction_type = "release" if decision == "release_unanswered" else "finalize"
        billing_status = "released" if transaction_type == "release" else "finalized"
        resolution_payload = {
            "tenant_id": tenant_id,
            "call_id": call_id,
            "hold_reason": hold_reason,
            "decision": decision,
            "evidence_type": evidence_type,
            "evidence_reference": evidence_reference,
            "evidence_sha256": evidence_sha256,
            "adjudication_reason": adjudication_reason,
            "authoritative_duration_seconds": duration,
            "authoritative_cost": (
                str(authoritative_cost) if authoritative_cost is not None else None
            ),
            "authoritative_currency": authoritative_currency,
        }
        resolution_hash = hashlib.sha256(
            json.dumps(
                resolution_payload,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        request_payload = {
            **resolution_payload,
            "resolution_hash": resolution_hash,
            "approval_action": approval_action,
            "approval_request_id": approval_request_id,
            "actor_id": actor_id,
            "actor_role": "platform_admin",
            "idempotency_key": request_id,
        }
        request_hash = hashlib.sha256(
            json.dumps(
                request_payload,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        resolution_metadata = {
            **resolution_payload,
            "resolution_hash": resolution_hash,
            "approval_action": approval_action,
            "approval_request_id": approval_request_id,
            "requested_by": None,
            "approved_by": actor_id if approval_action == "approve" else None,
            "approval_idempotency_key": (request_id if approval_action == "approve" else None),
            "actor_id": actor_id,
            "actor_role": "platform_admin",
            "idempotency_key": request_id,
            "request_hash": request_hash,
            "manual_hold_resolution": True,
        }
        scope_key = f"tenant:{tenant_id}"
        operation = (
            f"{approval_action}_inbound_billing_hold_finalize"
            if transaction_type == "finalize"
            else "resolve_inbound_billing_hold"
        )
        ledger_key = f"inbound:hold-resolution:{call_id}"
        inserted_resolution = False

        def _result_from_response(raw: Any) -> InboundHoldResolutionResult:
            response = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
            response["is_replay"] = True
            return InboundHoldResolutionResult(**response)

        def _settlement_matches(row: Mapping[str, Any]) -> bool:
            row_amount = row.get("amount")
            try:
                normalized_row_amount = Decimal(str(row_amount)) if row_amount is not None else None
            except (InvalidOperation, ValueError):
                return False
            row_currency = str(row.get("currency") or "").strip().upper() or None
            return bool(
                str(row.get("transaction_type") or "") == transaction_type
                and str(row.get("idempotency_key") or "") == ledger_key
                and normalized_row_amount == authoritative_cost
                and row_currency == authoritative_currency
                and _json_obj(row.get("metadata")) == resolution_metadata
            )

        async with acquire_with_tenant(self._pool, tenant_id) as conn:
            # Use the exact same lock domain as admission/quota reads. It must
            # precede the row lock so every tenant accounting writer has one
            # deterministic lock order.
            await conn.execute(
                "SELECT pg_advisory_xact_lock($1::bigint)",
                self._limiter._lock_key(tenant_id),
            )

            claim = await conn.fetchrow(
                """
                INSERT INTO inbound_operation_idempotency (
                    tenant_id, scope_key, operation, idempotency_key,
                    request_hash, actor_id
                ) VALUES ($1,$2,$3,$4,$5,$6)
                ON CONFLICT (scope_key, operation, idempotency_key) DO NOTHING
                RETURNING id
                """,
                tenant_id,
                scope_key,
                operation,
                request_id,
                request_hash,
                actor_id,
            )
            if not claim:
                replay = await conn.fetchrow(
                    """
                    SELECT request_hash, response_body, status_code
                    FROM inbound_operation_idempotency
                    WHERE tenant_id=$1::uuid AND scope_key=$2
                      AND operation=$3 AND idempotency_key=$4
                    """,
                    tenant_id,
                    scope_key,
                    operation,
                    request_id,
                )
                if not replay or str(replay.get("request_hash") or "") != request_hash:
                    raise InboundHoldResolutionConflictError(
                        "Idempotency-Key was reused with a different resolution request"
                    )
                if replay.get("response_body") is None or replay.get("status_code") is None:
                    raise InboundHoldResolutionConflictError(
                        "billing hold resolution is already in progress"
                    )
                return _result_from_response(replay["response_body"])

            call = await conn.fetchrow(
                """
                SELECT id, tenant_id, provider, provider_call_id, direction,
                       status, outcome, ended_at, duration_seconds, cost,
                       reserved_seconds, billing_status, billing_hold_reason
                FROM calls
                WHERE id=$1::uuid AND tenant_id=$2::uuid AND direction='inbound'
                FOR UPDATE
                """,
                call_id,
                tenant_id,
            )
            if not call:
                raise InboundHoldResolutionNotFoundError(
                    "Inbound call was not found in the selected tenant"
                )
            if (
                str(call.get("status") or "").strip().lower() not in _TERMINAL_STATUSES
                or call.get("ended_at") is None
            ):
                raise InboundHoldResolutionConflictError(
                    "Only a terminal ended inbound call can have its billing hold resolved"
                )

            approval: Optional[Mapping[str, Any]] = None
            approval_pending = False
            approval_approved_by: Optional[str] = None
            if approval_action == "approve":
                approval = await conn.fetchrow(
                    """
                    SELECT id, tenant_id, call_id, hold_reason, evidence_type,
                           evidence_reference, evidence_sha256,
                           adjudication_reason,
                           authoritative_duration_seconds,
                           authoritative_cost, authoritative_currency,
                           resolution_hash, requested_by, request_id, status,
                           approved_by, approval_idempotency_key, approved_at
                    FROM inbound_billing_hold_finalize_approvals
                    WHERE id=$1::uuid AND tenant_id=$2::uuid
                      AND call_id=$3::uuid
                    FOR UPDATE
                    """,
                    approval_request_id,
                    tenant_id,
                    call_id,
                )
                if not approval:
                    raise InboundHoldResolutionConflictError(
                        "The finalize approval request was not found for this tenant and call"
                    )
                if str(approval.get("resolution_hash") or "") != resolution_hash:
                    raise InboundHoldResolutionConflictError(
                        "Finalize approval evidence does not match the immutable request"
                    )
                requested_by = str(approval.get("requested_by") or "")
                if requested_by == actor_id:
                    raise InboundHoldResolutionConflictError(
                        "The finalize requester cannot approve their own request"
                    )
                approval_status = str(approval.get("status") or "")
                if approval_status == "pending":
                    approval_pending = True
                    approval_approved_by = actor_id
                elif approval_status == "approved":
                    approval_approved_by = str(approval.get("approved_by") or "")
                    if (
                        approval_approved_by != actor_id
                        or str(approval.get("approval_idempotency_key") or "") != request_id
                    ):
                        raise InboundHoldResolutionConflictError(
                            "Finalize approval was already consumed by a different "
                            "approver or idempotency key"
                        )
                else:
                    raise InboundHoldResolutionConflictError(
                        "Finalize approval has an invalid durable status"
                    )
                resolution_metadata = {
                    **resolution_metadata,
                    "requested_by": requested_by,
                    "approved_by": approval_approved_by,
                }

            existing_settlement = await conn.fetchrow(
                """
                SELECT id, transaction_type, quantity_seconds, amount, currency,
                       idempotency_key, policy_snapshot, metadata
                FROM inbound_usage_transactions
                WHERE tenant_id=$1::uuid AND call_id=$2::uuid
                  AND call_leg_id IS NULL
                  AND transaction_type IN ('release','finalize')
                ORDER BY created_at ASC
                LIMIT 1
                """,
                tenant_id,
                call_id,
            )
            if existing_settlement:
                if approval_action == "request":
                    raise InboundHoldResolutionConflictError(
                        "Inbound billing hold already has an immutable settlement"
                    )
                if not _settlement_matches(existing_settlement):
                    raise InboundHoldResolutionConflictError(
                        "Inbound billing hold already has a different immutable settlement"
                    )
                if (
                    str(call.get("billing_status") or "") != billing_status
                    or call.get("billing_hold_reason") is not None
                    or int(call.get("duration_seconds") or 0) != duration
                ):
                    raise InboundHoldResolutionConflictError(
                        "Inbound call projection disagrees with its immutable settlement"
                    )
                result = InboundHoldResolutionResult(
                    call_id=call_id,
                    tenant_id=tenant_id,
                    hold_reason=hold_reason,
                    decision=decision,
                    billing_status=billing_status,
                    duration_seconds=duration,
                    usage_transaction_id=str(existing_settlement["id"]),
                    evidence_type=evidence_type,
                    evidence_reference=evidence_reference,
                    evidence_sha256=evidence_sha256,
                    authoritative_currency=authoritative_currency,
                    approval_request_id=approval_request_id,
                    requested_by=(str(approval.get("requested_by")) if approval else None),
                    approved_by=approval_approved_by,
                    is_replay=True,
                )
                await conn.execute(
                    """
                    UPDATE inbound_operation_idempotency
                    SET response_body=$5::jsonb, status_code=200,
                        resource_type='call', resource_id=$6::uuid
                    WHERE tenant_id=$1::uuid AND scope_key=$2
                      AND operation=$3 AND idempotency_key=$4
                      AND request_hash=$7
                    """,
                    tenant_id,
                    scope_key,
                    operation,
                    request_id,
                    json.dumps(result.to_dict()),
                    call_id,
                    request_hash,
                )
                return result

            if (
                str(call.get("billing_status") or "") != "held"
                or str(call.get("billing_hold_reason") or "") != hold_reason
            ):
                raise InboundHoldResolutionConflictError(
                    "Inbound call is not held for the expected reason"
                )
            if approval_action == "approve" and not approval_pending:
                raise InboundHoldResolutionConflictError(
                    "Approved finalize request has no matching immutable settlement"
                )

            unsettled_transfer_usage = await conn.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM call_legs transfer_leg
                    JOIN calls parent_call ON parent_call.id=transfer_leg.call_id
                    WHERE transfer_leg.call_id=$1::uuid
                      AND parent_call.tenant_id=$2::uuid
                      AND transfer_leg.leg_type='transfer'
                      AND (
                          transfer_leg.status IN ('initiated','ringing','answered')
                          OR transfer_leg.billing_status
                              NOT IN ('finalized','released','reversed')
                      )
                )
                """,
                call_id,
                tenant_id,
            )
            if unsettled_transfer_usage is True:
                raise InboundHoldResolutionConflictError(
                    "Inbound transfer usage must be resolved before its parent hold"
                )

            reserve = await conn.fetchrow(
                """
                SELECT id, quantity_seconds, currency
                FROM inbound_usage_transactions
                WHERE tenant_id=$1::uuid AND call_id=$2::uuid
                  AND call_leg_id IS NULL AND transaction_type='reserve'
                """,
                tenant_id,
                call_id,
            )
            if not reserve:
                raise InboundHoldResolutionConflictError(
                    "Inbound billing hold has no immutable usage reservation"
                )
            reserve_currency = str(reserve.get("currency") or "").strip().upper() or None
            if (
                authoritative_currency is not None
                and reserve_currency is not None
                and authoritative_currency != reserve_currency
            ):
                raise InboundHoldResolutionConflictError(
                    "Authoritative currency conflicts with the immutable usage reservation"
                )
            reserved = max(0, int(reserve.get("quantity_seconds") or 0))
            quantity_delta = -reserved if transaction_type == "release" else duration - reserved

            if approval_action == "request":
                approval = await conn.fetchrow(
                    """
                    INSERT INTO inbound_billing_hold_finalize_approvals (
                        tenant_id, call_id, hold_reason, evidence_type,
                        evidence_reference, evidence_sha256,
                        adjudication_reason,
                        authoritative_duration_seconds,
                        authoritative_cost, authoritative_currency,
                        resolution_hash, requested_by, request_id
                    ) VALUES (
                        $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13
                    )
                    ON CONFLICT (tenant_id, call_id) DO NOTHING
                    RETURNING id, requested_by, request_id, resolution_hash,
                              status
                    """,
                    tenant_id,
                    call_id,
                    hold_reason,
                    evidence_type,
                    evidence_reference,
                    evidence_sha256,
                    adjudication_reason,
                    duration,
                    authoritative_cost,
                    authoritative_currency,
                    resolution_hash,
                    actor_id,
                    request_id,
                )
                if not approval:
                    approval = await conn.fetchrow(
                        """
                        SELECT id, requested_by, request_id, resolution_hash,
                               status
                        FROM inbound_billing_hold_finalize_approvals
                        WHERE tenant_id=$1::uuid AND call_id=$2::uuid
                        FOR UPDATE
                        """,
                        tenant_id,
                        call_id,
                    )
                    if (
                        not approval
                        or str(approval.get("resolution_hash") or "") != resolution_hash
                        or str(approval.get("requested_by") or "") != actor_id
                        or str(approval.get("request_id") or "") != request_id
                        or str(approval.get("status") or "") != "pending"
                    ):
                        raise InboundHoldResolutionConflictError(
                            "A different immutable finalize approval request exists "
                            "for this call"
                        )

                approval_id = str(approval["id"])
                await conn.execute(
                    """
                    INSERT INTO inbound_audit_events (
                        tenant_id, event_type, actor_id, actor_role,
                        resource_type, resource_id, reason,
                        before_state, after_state, metadata, idempotency_key
                    ) VALUES (
                        $1,'inbound_billing_hold_finalize_requested',$2,$3,
                        'call',$4,$5,$6::jsonb,$7::jsonb,$8::jsonb,$9
                    )
                    """,
                    tenant_id,
                    actor_id,
                    "platform_admin",
                    call_id,
                    adjudication_reason,
                    json.dumps(
                        {
                            "billing_status": "held",
                            "billing_hold_reason": hold_reason,
                            "duration_seconds": int(call.get("duration_seconds") or 0),
                            "cost": (str(call["cost"]) if call.get("cost") is not None else None),
                        }
                    ),
                    json.dumps(
                        {
                            "billing_status": "held",
                            "billing_hold_reason": hold_reason,
                            "approval_status": "pending",
                        }
                    ),
                    json.dumps(
                        {
                            **resolution_payload,
                            "resolution_hash": resolution_hash,
                            "approval_request_id": approval_id,
                            "requested_by": actor_id,
                        }
                    ),
                    request_id,
                )
                result = InboundHoldResolutionResult(
                    call_id=call_id,
                    tenant_id=tenant_id,
                    hold_reason=hold_reason,
                    decision=decision,
                    billing_status="held",
                    duration_seconds=duration,
                    usage_transaction_id=None,
                    evidence_type=evidence_type,
                    evidence_reference=evidence_reference,
                    evidence_sha256=evidence_sha256,
                    authoritative_currency=authoritative_currency,
                    workflow_status="pending_approval",
                    approval_request_id=approval_id,
                    requested_by=actor_id,
                )
                stored = await conn.fetchrow(
                    """
                    UPDATE inbound_operation_idempotency
                    SET response_body=$5::jsonb, status_code=202,
                        resource_type='call', resource_id=$6::uuid
                    WHERE tenant_id=$1::uuid AND scope_key=$2
                      AND operation=$3 AND idempotency_key=$4
                      AND request_hash=$7
                    RETURNING id
                    """,
                    tenant_id,
                    scope_key,
                    operation,
                    request_id,
                    json.dumps(result.to_dict()),
                    call_id,
                    request_hash,
                )
                if not stored:
                    raise RuntimeError("billing hold approval request was not stored")
                self._usage_metric("finalize", "manual_hold_approval_requested")
                return result

            if approval_action == "approve":
                # FOR SHARE keeps a concurrent switch-off writer behind this
                # transaction.  This is deliberately checked only during final
                # approval, immediately before approval and ledger mutation.
                settlement_controls = await conn.fetchrow(
                    """
                    SELECT inbound_settlement_enabled
                    FROM platform_runtime_controls
                    WHERE id=1
                    FOR SHARE
                    """
                )
                if (
                    not settlement_controls
                    or settlement_controls.get("inbound_settlement_enabled") is not True
                ):
                    raise InboundHoldResolutionConflictError(
                        "Inbound settlement is disabled; this hold cannot be finalized"
                    )
                approved = await conn.fetchrow(
                    """
                    UPDATE inbound_billing_hold_finalize_approvals
                    SET status='approved', approved_by=$4::uuid,
                        approval_idempotency_key=$5, approved_at=NOW()
                    WHERE id=$1::uuid AND tenant_id=$2::uuid
                      AND call_id=$3::uuid AND status='pending'
                      AND resolution_hash=$6 AND requested_by <> $4::uuid
                    RETURNING requested_by, approved_by
                    """,
                    approval_request_id,
                    tenant_id,
                    call_id,
                    actor_id,
                    request_id,
                    resolution_hash,
                )
                if not approved:
                    raise InboundHoldResolutionConflictError(
                        "Finalize approval changed or was already consumed"
                    )

            usage = await conn.fetchrow(
                """
                INSERT INTO inbound_usage_transactions (
                    tenant_id, call_id, call_leg_id, transaction_type,
                    quantity_seconds, amount, currency, idempotency_key,
                    related_transaction_id, policy_snapshot, metadata
                ) VALUES ($1,$2,NULL,$3,$4,$5,$6,$7,$8,$9::jsonb,$10::jsonb)
                ON CONFLICT DO NOTHING
                RETURNING id, transaction_type, quantity_seconds, amount, currency,
                          idempotency_key, policy_snapshot, metadata
                """,
                tenant_id,
                call_id,
                transaction_type,
                quantity_delta,
                authoritative_cost,
                authoritative_currency,
                ledger_key,
                reserve["id"],
                json.dumps(
                    {
                        "reserved_seconds": reserved,
                        "actual_seconds": duration,
                    }
                ),
                json.dumps(resolution_metadata),
            )
            if not usage:
                usage = await conn.fetchrow(
                    """
                    SELECT id, transaction_type, quantity_seconds, amount, currency,
                           idempotency_key, policy_snapshot, metadata
                    FROM inbound_usage_transactions
                    WHERE tenant_id=$1::uuid AND call_id=$2::uuid
                      AND call_leg_id IS NULL
                      AND transaction_type IN ('release','finalize')
                    ORDER BY created_at ASC
                    LIMIT 1
                    """,
                    tenant_id,
                    call_id,
                )
                if not usage or not _settlement_matches(usage):
                    raise InboundHoldResolutionConflictError(
                        "Concurrent inbound hold resolution chose a different settlement"
                    )
            else:
                inserted_resolution = True

            updated = await conn.fetchrow(
                """
                UPDATE calls
                SET duration_seconds=$4,
                    cost=COALESCE($5,cost),
                    billing_status=$6, billing_hold_reason=NULL,
                    updated_at=NOW()
                WHERE id=$1::uuid AND tenant_id=$2::uuid AND direction='inbound'
                  AND billing_status='held' AND billing_hold_reason=$3
                RETURNING id, billing_status, billing_hold_reason,
                          duration_seconds, cost
                """,
                call_id,
                tenant_id,
                hold_reason,
                duration,
                authoritative_cost,
                billing_status,
            )
            if not updated:
                raise InboundHoldResolutionConflictError(
                    "Inbound billing hold changed before the resolution CAS"
                )

            await conn.execute(
                """
                INSERT INTO inbound_audit_events (
                    tenant_id, event_type, actor_id, actor_role,
                    resource_type, resource_id, reason,
                    before_state, after_state, metadata, idempotency_key
                ) VALUES (
                    $1,'inbound_billing_hold_resolved',$2,$3,
                    'call',$4,$5,$6::jsonb,$7::jsonb,$8::jsonb,$9
                )
                """,
                tenant_id,
                actor_id,
                "platform_admin",
                call_id,
                adjudication_reason,
                json.dumps(
                    {
                        "billing_status": "held",
                        "billing_hold_reason": hold_reason,
                        "duration_seconds": int(call.get("duration_seconds") or 0),
                        "cost": str(call["cost"]) if call.get("cost") is not None else None,
                    }
                ),
                json.dumps(
                    {
                        "billing_status": billing_status,
                        "billing_hold_reason": None,
                        "duration_seconds": duration,
                        "cost": (str(updated["cost"]) if updated.get("cost") is not None else None),
                    }
                ),
                json.dumps(
                    {
                        "decision": decision,
                        "evidence_type": evidence_type,
                        "evidence_reference": evidence_reference,
                        "evidence_sha256": evidence_sha256,
                        "authoritative_cost": (
                            str(authoritative_cost) if authoritative_cost is not None else None
                        ),
                        "authoritative_currency": authoritative_currency,
                        "usage_transaction_id": str(usage["id"]),
                        "resolution_hash": resolution_hash,
                        "approval_request_id": approval_request_id,
                        "requested_by": (str(approval.get("requested_by")) if approval else None),
                        "approved_by": approval_approved_by,
                        "request_hash": request_hash,
                    }
                ),
                request_id,
            )
            result = InboundHoldResolutionResult(
                call_id=call_id,
                tenant_id=tenant_id,
                hold_reason=hold_reason,
                decision=decision,
                billing_status=billing_status,
                duration_seconds=duration,
                usage_transaction_id=str(usage["id"]),
                evidence_type=evidence_type,
                evidence_reference=evidence_reference,
                evidence_sha256=evidence_sha256,
                authoritative_currency=authoritative_currency,
                approval_request_id=approval_request_id,
                requested_by=(str(approval.get("requested_by")) if approval else None),
                approved_by=approval_approved_by,
            )
            stored = await conn.fetchrow(
                """
                UPDATE inbound_operation_idempotency
                SET response_body=$5::jsonb, status_code=200,
                    resource_type='call', resource_id=$6::uuid
                WHERE tenant_id=$1::uuid AND scope_key=$2
                  AND operation=$3 AND idempotency_key=$4
                  AND request_hash=$7
                RETURNING id
                """,
                tenant_id,
                scope_key,
                operation,
                request_id,
                json.dumps(result.to_dict()),
                call_id,
                request_hash,
            )
            if not stored:
                raise RuntimeError("billing hold idempotency response was not stored")

        self._usage_metric(
            "release" if transaction_type == "release" else "finalize",
            "manual_hold_resolution" if inserted_resolution else "duplicate_replay",
        )
        return result

    async def heartbeat_active_call(
        self,
        *,
        call_id: str,
        provider: str,
        provider_call_id: str,
        request_id: Optional[str] = None,
    ) -> bool:
        """Refresh the lease for one still-active admitted inbound call.

        The durable call UUID and provider identity must both match. This
        prevents a stale callback from extending another call's concurrency
        slot. Telephony should call this every 30 seconds while media is live.
        """

        normalized_provider = _provider(provider)
        normalized_provider_call_id = _call_id(provider_call_id)
        try:
            normalized_call_id = str(uuid.UUID(str(call_id)))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("call_id must be a UUID") from exc
        if not normalized_provider or not normalized_provider_call_id:
            raise ValueError("provider and provider_call_id are required")

        async with acquire_with_tenant(self._pool, None) as conn:
            call = await conn.fetchrow(
                """
                SELECT tenant_id, concurrency_lease_id
                FROM calls
                WHERE id=$1 AND direction='inbound' AND provider=$2
                  AND provider_call_id=$3
                  AND admission_status='allowed'
                  AND processing_status='active'
                  AND billing_status='reserved'
                """,
                normalized_call_id,
                normalized_provider,
                normalized_provider_call_id,
            )
            if not call or not call["concurrency_lease_id"]:
                return False
            base_refreshed = bool(
                await self._limiter.heartbeat_lease(
                    conn,
                    tenant_id=str(call["tenant_id"]),
                    lease_id=call["concurrency_lease_id"],
                    request_id=request_id,
                )
            )
            if not base_refreshed:
                return False
            transfer_leases = await conn.fetch(
                """
                SELECT id
                FROM tenant_telephony_concurrency_leases
                WHERE tenant_id=$1 AND call_id=$2::uuid
                  AND lease_kind='transfer'
                  AND state IN ('active','releasing')
                  AND released_at IS NULL
                FOR UPDATE
                """,
                call["tenant_id"],
                normalized_call_id,
            )
            for transfer_lease in transfer_leases:
                if not await self._limiter.heartbeat_lease(
                    conn,
                    tenant_id=str(call["tenant_id"]),
                    lease_id=transfer_lease["id"],
                    request_id=(
                        f"{request_id}:transfer" if request_id else "inbound-transfer-heartbeat"
                    ),
                ):
                    return False
            return True

    async def reverse_finalized_usage(
        self,
        *,
        call_id: str,
        provider: str,
        provider_call_id: str,
        reason: str,
        request_id: Optional[str] = None,
    ) -> InboundFinalizationResult:
        """Append one compensating reversal for an already-finalized call.

        The reserve and finalize rows remain immutable.  The reversal points
        at the finalize row and negates the call's complete net usage
        (reserve + finalize delta), so the ledger sums back to zero.  The
        database permits exactly one reversal per settlement.
        """

        normalized_provider = _provider(provider)
        normalized_call_identity = _call_id(provider_call_id)
        try:
            call_uuid = str(uuid.UUID(str(call_id)))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("call_id must be a UUID") from exc
        if not normalized_provider or not normalized_call_identity:
            raise ValueError("provider and provider_call_id are required")
        if not str(reason or "").strip():
            raise ValueError("reason is required for a billing reversal")

        async with acquire_with_tenant(self._pool, None) as conn:
            call = await conn.fetchrow(
                """
                SELECT * FROM calls
                WHERE id=$1 AND direction='inbound' AND provider=$2
                  AND provider_call_id=$3
                FOR UPDATE
                """,
                call_uuid,
                normalized_provider,
                normalized_call_identity,
            )
            if not call:
                raise LookupError("Inbound call not found for provider identity")
            settlement = await conn.fetchrow(
                """
                SELECT id, transaction_type, amount, currency
                FROM inbound_usage_transactions
                WHERE call_id=$1 AND call_leg_id IS NULL
                  AND transaction_type IN ('finalize','release')
                ORDER BY created_at LIMIT 1
                """,
                call_uuid,
            )
            if not settlement or settlement["transaction_type"] != "finalize":
                raise ValueError("Only finalized inbound usage can be reversed")
            existing = await conn.fetchrow(
                """
                SELECT id FROM inbound_usage_transactions
                WHERE related_transaction_id=$1 AND transaction_type='reverse'
                """,
                settlement["id"],
            )
            if existing:
                return InboundFinalizationResult(
                    finalized=True,
                    reason="duplicate_replay",
                    call_id=call_uuid,
                    billing_status="reversed",
                    lease_released=True,
                    usage_transaction_id=str(existing["id"]),
                    is_replay=True,
                )
            billed_seconds = int(
                await conn.fetchval(
                    """
                    SELECT COALESCE(SUM(quantity_seconds),0)
                    FROM inbound_usage_transactions
                    WHERE call_id=$1 AND call_leg_id IS NULL
                      AND transaction_type IN ('reserve','finalize')
                    """,
                    call_uuid,
                )
                or 0
            )
            reversal_key = f"inbound:reverse:{normalized_provider}:{normalized_call_identity}"
            reversal = await conn.fetchrow(
                """
                INSERT INTO inbound_usage_transactions (
                    tenant_id, call_id, call_leg_id, transaction_type,
                    quantity_seconds, amount, currency, idempotency_key,
                    related_transaction_id, policy_snapshot, metadata
                ) VALUES ($1,$2,NULL,'reverse',$3,$4,$5,$6,$7,$8::jsonb,$9::jsonb)
                ON CONFLICT (tenant_id, idempotency_key) DO NOTHING
                RETURNING id
                """,
                call["tenant_id"],
                call_uuid,
                -billed_seconds,
                -settlement["amount"] if settlement["amount"] is not None else None,
                settlement.get("currency"),
                reversal_key,
                settlement["id"],
                json.dumps({"billed_seconds": billed_seconds}),
                json.dumps({"reason": reason, "request_id": request_id}),
            )
            if not reversal:
                reversal = await conn.fetchrow(
                    """
                    SELECT id FROM inbound_usage_transactions
                    WHERE tenant_id=$1 AND idempotency_key=$2
                    """,
                    call["tenant_id"],
                    reversal_key,
                )
            await conn.execute(
                """
                UPDATE calls
                SET billing_status='reversed', cost=0, updated_at=NOW()
                WHERE id=$1
                """,
                call_uuid,
            )
            result = InboundFinalizationResult(
                finalized=True,
                reason="reversed",
                call_id=call_uuid,
                billing_status="reversed",
                lease_released=True,
                usage_transaction_id=str(reversal["id"]),
            )
        self._usage_metric("reverse", "inserted")
        return result

    async def reconcile_reenabled_settlement_holds(
        self,
        *,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Finalize terminal holds created solely by the settlement switch.

        The terminal callback already persisted ``ended_at`` before creating
        the hold, so this is a database-only billing transition. It does not
        depend on provider-specific hangup confirmation or volatile lifecycle
        dedupe caches. ``finalize`` locks each row and the immutable ledger's
        unique key makes concurrent watchdog attempts idempotent. Usage-over-
        reservation and unknown historical holds are deliberately excluded.
        """

        batch_limit = max(1, min(int(limit), 500))
        async with acquire_with_tenant(self._pool, None) as conn:
            rows = list(
                await conn.fetch(
                    """
                    SELECT id, provider, provider_call_id, status,
                           duration_seconds, cost, outcome
                    FROM calls
                    WHERE direction='inbound'
                      AND billing_status='held'
                      AND billing_hold_reason='settlement_switch_disabled'
                      AND ended_at IS NOT NULL
                      AND status = ANY($1::text[])
                      AND provider IS NOT NULL
                      AND BTRIM(provider) <> ''
                      AND provider_call_id IS NOT NULL
                      AND BTRIM(provider_call_id) <> ''
                      AND EXISTS (
                          SELECT 1
                          FROM platform_runtime_controls settlement_controls
                          WHERE settlement_controls.id=1
                            AND settlement_controls.inbound_settlement_enabled
                      )
                    ORDER BY updated_at
                    LIMIT $2
                    """,
                    list(_TERMINAL_STATUSES),
                    batch_limit,
                )
            )

        items: list[dict[str, Any]] = []
        reconciled = 0
        for row_value in rows:
            row = dict(row_value)
            call_id = str(row.get("id") or "")
            try:
                result = await self.finalize(
                    InboundFinalizationRequest(
                        call_id=call_id,
                        provider=str(row.get("provider") or ""),
                        provider_call_id=str(row.get("provider_call_id") or ""),
                        terminal_status=str(row.get("status") or ""),
                        duration_seconds=max(
                            0,
                            int(row.get("duration_seconds") or 0),
                        ),
                        cost=row.get("cost"),
                        outcome=row.get("outcome"),
                        reason="settlement_switch_reenabled",
                        request_id=f"settlement-switch-reenabled:{call_id}",
                    )
                )
            except Exception as exc:  # noqa: BLE001 - isolate each durable row
                logger.error(
                    "inbound_switch_hold_reconcile_failed call=%s err=%s",
                    call_id[:12],
                    exc,
                )
                # A permanently malformed/blocked oldest row must not occupy
                # this bounded batch forever. Move only the still-eligible
                # retry behind its peers; a concurrent successful finalizer
                # changes the guarded status/reason and is never touched.
                try:
                    async with acquire_with_tenant(self._pool, None) as conn:
                        await conn.execute(
                            """
                            UPDATE calls
                               SET updated_at=NOW()
                             WHERE id=$1::uuid
                               AND billing_status='held'
                               AND billing_hold_reason='settlement_switch_disabled'
                            """,
                            call_id,
                        )
                except Exception as rotate_exc:  # noqa: BLE001 - retry next pass
                    logger.warning(
                        "inbound_switch_hold_retry_rotation_failed call=%s err=%s",
                        call_id[:12],
                        rotate_exc,
                    )
                continue
            if result.finalized:
                reconciled += 1
            items.append(
                {
                    "call_id": call_id,
                    "billing_status": result.billing_status,
                    "transaction_type": result.reason,
                    "status": str(row.get("status") or ""),
                }
            )
        return {"reconciled": reconciled, "items": items}

    async def reconcile_stale_reservations(
        self,
        *,
        max_age_seconds: int = 7200,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Surface stale reservations to proof-aware telephony recovery.

        Age, an expired lease heartbeat, or a missing lease is never PBX
        absence proof. This watchdog therefore performs no usage transaction,
        billing transition, lease release, ``ended_at`` write, or terminal
        status projection. It only moves a still-nonterminal row to the honest
        ``termination_pending`` state. The telephony owner discovers that row
        on its <=30-second recovery pass and may settle it only after confirmed
        parent/transfer-leg termination. Already-terminal reserved rows are
        merely surfaced; the same recovery scan already selects them directly.
        """

        age = max(60, min(int(max_age_seconds), 24 * 60 * 60))
        batch_limit = max(1, min(int(limit), 500))
        reconciled: list[dict[str, Any]] = []
        async with acquire_with_tenant(self._pool, None) as conn:
            rows = await conn.fetch(
                """
                SELECT c.*
                FROM calls c
                LEFT JOIN tenant_telephony_concurrency_leases lease
                  ON lease.id=c.concurrency_lease_id
                LEFT JOIN tenant_telephony_concurrency_policies policy
                  ON policy.id=lease.policy_id
                WHERE c.direction='inbound'
                  AND c.billing_status='reserved'
                  AND NOT (
                      LOWER(COALESCE(c.status,'')) = ANY($3::text[])
                  )
                  AND c.updated_at < NOW() - ($1::int * INTERVAL '1 second')
                  AND (
                        c.ended_at IS NOT NULL
                        OR c.processing_status IN ('completed','failed','released')
                        OR lease.id IS NULL
                        OR lease.released_at IS NOT NULL
                        OR lease.state IN ('released','expired')
                        OR lease.last_heartbeat_at < NOW() - (
                            COALESCE(policy.lease_ttl_seconds,120)
                            + COALESCE(policy.heartbeat_grace_seconds,30)
                        ) * INTERVAL '1 second'
                  )
                ORDER BY c.updated_at
                FOR UPDATE OF c SKIP LOCKED
                LIMIT $2
                """,
                age,
                batch_limit,
                list(TERMINAL_CALL_STATUSES),
            )
            for raw in rows:
                call = dict(raw)
                await conn.execute(
                    """
                    UPDATE calls
                    SET status='termination_pending', updated_at=NOW()
                    WHERE id=$1
                      AND billing_status='reserved'
                      AND NOT (LOWER(COALESCE(status,'')) = ANY($2::text[]))
                    """,
                    call["id"],
                    list(TERMINAL_CALL_STATUSES),
                )
                reconciled.append(
                    {
                        "call_id": str(call["id"]),
                        "billing_status": str(call.get("billing_status") or ""),
                        "transaction_type": "termination_pending",
                        "status": "termination_pending",
                    }
                )
        for item in reconciled:
            self._usage_metric("recovery", "queued")
        return {"reconciled": len(reconciled), "items": reconciled}


async def admit_inbound_call(db_pool: Any, **kwargs: Any) -> dict[str, Any]:
    """Small callback adapter for telephony integrations."""

    return (
        await InboundAdmissionService(db_pool).admit(InboundAdmissionRequest(**kwargs))
    ).to_dict()


async def finalize_inbound_call(db_pool: Any, **kwargs: Any) -> dict[str, Any]:
    """Small terminal callback adapter for telephony integrations."""

    return (
        await InboundAdmissionService(db_pool).finalize(InboundFinalizationRequest(**kwargs))
    ).to_dict()


async def reconcile_stale_inbound_reservations(
    db_pool: Any, *, max_age_seconds: int = 7200, limit: int = 100
) -> dict[str, Any]:
    """Scheduler/startup-safe callable for the orphan reservation watchdog."""

    return await InboundAdmissionService(db_pool).reconcile_stale_reservations(
        max_age_seconds=max_age_seconds, limit=limit
    )


async def heartbeat_inbound_call(db_pool: Any, **kwargs: Any) -> bool:
    """Small callback adapter used by the active telephony lifecycle."""

    return await InboundAdmissionService(db_pool).heartbeat_active_call(**kwargs)


class InboundAdmissionWatchdog:
    """Single-flight periodic reconciler for durable inbound reservations."""

    def __init__(
        self,
        service: InboundAdmissionService,
        *,
        interval_seconds: int = 60,
        max_age_seconds: int = 7200,
        batch_limit: int = 100,
    ) -> None:
        self._service = service
        self._interval_seconds = max(10, min(int(interval_seconds), 3600))
        self._max_age_seconds = max(60, min(int(max_age_seconds), 24 * 60 * 60))
        self._batch_limit = max(1, min(int(batch_limit), 500))
        self._stop_event = asyncio.Event()
        self._task: Optional[asyncio.Task[None]] = None

    @property
    def running(self) -> bool:
        return bool(self._task and not self._task.done())

    async def run_once(self) -> dict[str, Any]:
        try:
            held = await self._service.reconcile_reenabled_settlement_holds(
                limit=self._batch_limit,
            )
        except Exception:
            logger.exception("inbound_switch_hold_watchdog_pass_failed")
            held = {"reconciled": 0, "items": []}
        stale = await self._service.reconcile_stale_reservations(
            max_age_seconds=self._max_age_seconds,
            limit=self._batch_limit,
        )
        return {
            "reconciled": int(held.get("reconciled", 0)) + int(stale.get("reconciled", 0)),
            "items": list(held.get("items") or []) + list(stale.get("items") or []),
        }

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                result = await self.run_once()
                if result.get("reconciled", 0):
                    logger.warning(
                        "inbound_admission_watchdog_reconciled count=%s",
                        result["reconciled"],
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                # One failed DB pass must not kill future reconciliation.
                logger.exception("inbound_admission_watchdog_pass_failed")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._interval_seconds)
            except asyncio.TimeoutError:
                continue

    def start(self) -> asyncio.Task[None]:
        if self.running:
            assert self._task is not None
            return self._task
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="inbound-admission-watchdog")
        return self._task

    async def stop(self) -> None:
        self._stop_event.set()
        task = self._task
        if not task:
            return
        try:
            await asyncio.wait_for(task, timeout=5)
        except asyncio.TimeoutError:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        finally:
            self._task = None


def start_inbound_admission_watchdog(
    db_pool: Any,
    *,
    interval_seconds: int = 60,
    max_age_seconds: int = 7200,
    batch_limit: int = 100,
) -> InboundAdmissionWatchdog:
    """Construct and start one application-scoped reservation watchdog."""

    watchdog = InboundAdmissionWatchdog(
        InboundAdmissionService(db_pool),
        interval_seconds=interval_seconds,
        max_age_seconds=max_age_seconds,
        batch_limit=batch_limit,
    )
    watchdog.start()
    return watchdog
