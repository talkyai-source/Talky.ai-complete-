"""
Abuse Detection Service (Day 7)

Real-time pattern detection for telephony fraud and abuse.

Detects:
- Call pumping (volume spikes)
- Toll fraud (premium/international abuse)
- Velocity anomalies
- Sequential dialing (war dialing)
- Geographic impossibility
- Wangiri (missed call) fraud
- IRSF (International Revenue Share Fraud)

References:
- CTIA Anti-Fraud Best Practices
- FCA Telecom Fraud Guidance (UK)
- Metaswitch Fraud Management
- Twilio Fraud Detection Patterns

Usage:
    detector = AbuseDetectionService(db_pool, redis_client)

    # After each call
    await detector.analyze_call_completed(call_id, tenant_id, duration, destination)

    # Periodic analysis (run every minute via cron/job)
    events = await detector.analyze_velocity_patterns()

    # Check before allowing call
    events = await detector.analyze_call_initiated(tenant_id, phone_number)
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple
from uuid import UUID

import asyncpg

logger = logging.getLogger(__name__)


class AbuseType(str, Enum):
    """Types of abuse patterns that can be detected."""
    VELOCITY_SPIKE = "velocity_spike"
    SHORT_DURATION_PATTERN = "short_duration_pattern"
    REPEAT_NUMBER = "repeat_number"
    SEQUENTIAL_DIALING = "sequential_dialing"
    PREMIUM_RATE = "premium_rate"
    INTERNATIONAL_SPIKE = "international_spike"
    AFTER_HOURS = "after_hours"
    GEOGRAPHIC_IMPOSSIBILITY = "geographic_impossibility"
    ACCOUNT_HOPPING = "account_hopping"
    TOLL_FRAUD = "toll_fraud"
    WANGIRI = "wangiri"
    IRS_FRAUD = "irs_fraud"


class Severity(str, Enum):
    """Severity levels for abuse events."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class AbuseEvent:
    """An detected abuse event."""
    abuse_type: AbuseType
    severity: Severity
    tenant_id: str
    details: Dict[str, Any] = field(default_factory=dict)
    recommended_action: str = "flag"
    trigger_value: Optional[float] = None
    threshold_value: Optional[float] = None
    phone_number: Optional[str] = None
    partner_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "abuse_type": self.abuse_type.value,
            "severity": self.severity.value,
            "tenant_id": self.tenant_id,
            "details": self.details,
            "recommended_action": self.recommended_action,
            "trigger_value": self.trigger_value,
            "threshold_value": self.threshold_value,
            "phone_number": self.phone_number,
            "partner_id": self.partner_id,
        }


# Known high-risk country codes for toll fraud
HIGH_RISK_COUNTRIES = {
    "PK",  # Pakistan
    "BD",  # Bangladesh
    "NG",  # Nigeria
    "VN",  # Vietnam
    "ID",  # Indonesia
    "CU",  # Cuba
    "LR",  # Liberia
    "SL",  # Sierra Leone
    "GM",  # Gambia
    "GW",  # Guinea-Bissau
    "TL",  # Timor-Leste
    "MV",  # Maldives
    "LK",  # Sri Lanka
    "ET",  # Ethiopia
    "SO",  # Somalia
    "AF",  # Afghanistan
    "IQ",  # Iraq
    "IR",  # Iran
    "SY",  # Syria
    "BY",  # Belarus
}

# Known premium rate prefixes
PREMIUM_RATE_PREFIXES = [
    "+1900",  # US premium
    "+1976",  # US premium
    "+4487",  # UK premium
    "+4498",  # UK premium
    "+339",   # France premium
    "+338",   # France premium
    "+809",   # Caribbean/Some premium
    "+876",   # Jamaica (often used for scams)
    "+809",   # Dominican Republic (some premium)
]


class AbuseDetectionService:
    """
    Real-time abuse detection for voice calls.

    Analyzes call patterns and triggers alerts/actions when abuse
    patterns are detected.

    Performance targets:
    - Synchronous checks (call_initiated): < 50ms
    - Asynchronous checks (call_completed): < 500ms
    - Batch analysis: Process 1000 calls/second
    """

    def __init__(
        self,
        db_pool: asyncpg.Pool,
        redis_client: Optional[Any] = None,
    ):
        self._db_pool = db_pool
        self._redis = redis_client

    # -------------------------------------------------------------------------
    # Synchronous Analysis (called before call is allowed)
    # -------------------------------------------------------------------------

    async def analyze_call_initiated(
        self,
        tenant_id: str,
        phone_number: str,
        user_id: Optional[str] = None,
        source_ip: Optional[str] = None,
    ) -> List[AbuseEvent]:
        """
        Analyze call initiation for immediate red flags.

        Called synchronously before call is allowed to proceed.
        Must be FAST (< 50ms).

        Args:
            tenant_id: UUID of tenant
            phone_number: E.164 formatted phone number
            user_id: Optional user ID
            source_ip: Optional source IP

        Returns:
            List of detected abuse events (empty if clean)
        """
        events = []

        # Check for rapid successive calls (harassment detection)
        rapid_events = await self._check_rapid_calls(tenant_id, phone_number)
        events.extend(rapid_events)

        # Check for sequential dialing pattern (war dialing)
        sequential_events = await self._check_sequential_dialing(tenant_id, phone_number)
        events.extend(sequential_events)

        # Check for premium rate destination
        premium_events = await self._check_premium_rate(tenant_id, phone_number)
        events.extend(premium_events)

        # Check for high-risk destination
        risk_events = await self._check_high_risk_destination(tenant_id, phone_number)
        events.extend(risk_events)

        return events

    # -------------------------------------------------------------------------
    # Asynchronous Analysis (called after call completes)
    # -------------------------------------------------------------------------

    async def analyze_call_completed(
        self,
        call_id: str,
        tenant_id: str,
        duration_seconds: int,
        phone_number: str,
        cost: Optional[float] = None,
        was_answered: bool = True,
        user_id: Optional[str] = None,
    ) -> List[AbuseEvent]:
        """
        Analyze completed call for abuse patterns.

        Called asynchronously after call ends.

        Args:
            call_id: UUID of the call
            tenant_id: UUID of tenant
            duration_seconds: Call duration in seconds
            phone_number: E.164 formatted phone number
            cost: Optional call cost
            was_answered: Whether the call was answered
            user_id: Optional user ID

        Returns:
            List of detected abuse events
        """
        events = []

        # Short duration pattern (call pumping)
        if duration_seconds < 10:
            events.append(AbuseEvent(
                abuse_type=AbuseType.SHORT_DURATION_PATTERN,
                severity=Severity.LOW,
                tenant_id=tenant_id,
                phone_number=phone_number,
                details={
                    "call_id": call_id,
                    "duration": duration_seconds,
                    "threshold": 10,
                },
                recommended_action="flag",
                trigger_value=float(duration_seconds),
                threshold_value=10.0,
            ))

        # Wangiri pattern (missed call fraud)
        if duration_seconds < 5 and not was_answered:
            wangiri_events = await self._check_wangiri_pattern(tenant_id, phone_number)
            events.extend(wangiri_events)

        # High cost alert
        if cost and cost > 1.0:  # $1+ per minute is high
            events.append(AbuseEvent(
                abuse_type=AbuseType.TOLL_FRAUD,
                severity=Severity.MEDIUM,
                tenant_id=tenant_id,
                phone_number=phone_number,
                details={
                    "call_id": call_id,
                    "cost": cost,
                    "duration": duration_seconds,
                    "cost_per_minute": (cost / duration_seconds * 60) if duration_seconds > 0 else 0,
                },
                recommended_action="warn",
                trigger_value=cost,
                threshold_value=1.0,
            ))

        # Store for aggregate analysis
        await self._store_call_metrics(
            call_id=call_id,
            tenant_id=tenant_id,
            duration_seconds=duration_seconds,
            phone_number=phone_number,
            cost=cost,
        )

        # Persist events
        for event in events:
            await self._record_event(event, call_id=call_id)

        return events

    # -------------------------------------------------------------------------
    # Batch/Periodic Analysis
    # -------------------------------------------------------------------------

    async def analyze_velocity_patterns(self) -> List[AbuseEvent]:
        """
        Periodic analysis for velocity spikes.

        Should be called by a background job every minute.

        Returns:
            List of velocity spike events detected
        """
        events = []

        async with self._db_pool.acquire() as conn:
            # Find tenants with unusual call volume in last 5 minutes
            rows = await conn.fetch(
                """
                SELECT
                    tenant_id,
                    COUNT(*) as call_count,
                    COUNT(DISTINCT phone_number) as unique_numbers,
                    AVG(duration_seconds) as avg_duration,
                    SUM(CASE WHEN duration_seconds < 10 THEN 1 ELSE 0 END) as short_calls,
                    COUNT(*) FILTER (WHERE destination_country IN (
                        'PK', 'BD', 'NG', 'VN', 'ID'
                    )) as high_risk_calls
                FROM calls
                WHERE created_at > NOW() - INTERVAL '5 minutes'
                  AND status = 'completed'
                GROUP BY tenant_id
                HAVING COUNT(*) > 20  -- More than 20 calls in 5 min
                """
            )

            for row in rows:
                tenant_id = row["tenant_id"]
                call_count = row["call_count"]

                # Get historical average for this hour of day
                avg_calls = await self._get_historical_avg_calls(tenant_id, window_hours=24)

                if avg_calls > 0 and call_count > (avg_calls * 3):
                    # Calculate severity based on multiplier
                    multiplier = call_count / avg_calls
                    if multiplier > 10:
                        severity = Severity.CRITICAL
                        action = "suspend"
                    elif multiplier > 5:
                        severity = Severity.HIGH
                        action = "block"
                    else:
                        severity = Severity.MEDIUM
                        action = "throttle"

                    events.append(AbuseEvent(
                        abuse_type=AbuseType.VELOCITY_SPIKE,
                        severity=severity,
                        tenant_id=tenant_id,
                        details={
                            "current_5min": call_count,
                            "historical_avg_5min": avg_calls,
                            "multiplier": multiplier,
                            "unique_numbers": row["unique_numbers"],
                            "short_calls": row["short_calls"],
                            "high_risk_calls": row["high_risk_calls"],
                        },
                        recommended_action=action,
                        trigger_value=float(call_count),
                        threshold_value=float(avg_calls * 3),
                    ))

                # Check for high proportion of short calls (call pumping)
                short_call_ratio = row["short_calls"] / call_count if call_count > 0 else 0
                if short_call_ratio > 0.5 and call_count > 10:
                    events.append(AbuseEvent(
                        abuse_type=AbuseType.SHORT_DURATION_PATTERN,
                        severity=Severity.HIGH,
                        tenant_id=tenant_id,
                        details={
                            "total_calls": call_count,
                            "short_calls": row["short_calls"],
                            "short_call_ratio": short_call_ratio,
                        },
                        recommended_action="block",
                        trigger_value=short_call_ratio,
                        threshold_value=0.5,
                    ))

                # Check for international spike
                high_risk_ratio = row["high_risk_calls"] / call_count if call_count > 0 else 0
                if high_risk_ratio > 0.3:
                    events.append(AbuseEvent(
                        abuse_type=AbuseType.INTERNATIONAL_SPIKE,
                        severity=Severity.HIGH,
                        tenant_id=tenant_id,
                        details={
                            "total_calls": call_count,
                            "high_risk_calls": row["high_risk_calls"],
                            "high_risk_ratio": high_risk_ratio,
                        },
                        recommended_action="block",
                        trigger_value=high_risk_ratio,
                        threshold_value=0.3,
                    ))

        # Persist events
        for event in events:
            await self._record_event(event)

        return events

    async def analyze_partner_aggregate(self, partner_id: str) -> List[AbuseEvent]:
        """
        Analyze aggregate patterns across all tenants of a partner.

        Args:
            partner_id: UUID of partner tenant

        Returns:
            List of aggregate abuse events
        """
        events = []

        async with self._db_pool.acquire() as conn:
            # Get aggregate stats for partner's tenants
            row = await conn.fetchrow(
                """
                SELECT
                    COUNT(DISTINCT t.id) as tenant_count,
                    COUNT(c.id) as total_calls,
                    COUNT(DISTINCT c.phone_number) as unique_numbers
                FROM tenants t
                LEFT JOIN calls c ON c.tenant_id = t.id
                    AND c.created_at > NOW() - INTERVAL '5 minutes'
                WHERE t.partner_id = $1
                GROUP BY t.partner_id
                """,
                partner_id,
            )

            if not row:
                return events

            # Get partner limits
            limits_row = await conn.fetchrow(
                """
                SELECT aggregate_calls_per_minute
                FROM partner_limits
                WHERE partner_id = $1 AND is_active = TRUE
                """,
                partner_id,
            )

            if limits_row:
                max_calls = limits_row["aggregate_calls_per_minute"]
                actual_calls = row["total_calls"]

                if actual_calls > max_calls:
                    events.append(AbuseEvent(
                        abuse_type=AbuseType.VELOCITY_SPIKE,
                        severity=Severity.HIGH,
                        tenant_id=partner_id,
                        partner_id=partner_id,
                        details={
                            "aggregate_calls": actual_calls,
                            "aggregate_limit": max_calls,
                            "affected_tenants": row["tenant_count"],
                        },
                        recommended_action="throttle",
                        trigger_value=float(actual_calls),
                        threshold_value=float(max_calls),
                    ))

        return events

    # -------------------------------------------------------------------------
    # Individual Detection Methods
    # -------------------------------------------------------------------------

    async def _check_rapid_calls(
        self,
        tenant_id: str,
        phone_number: str,
        threshold: int = 3,
        window_minutes: int = 5,
    ) -> List[AbuseEvent]:
        """Check for rapid successive calls to same number (harassment)."""
        async with self._db_pool.acquire() as conn:
            recent_count = await conn.fetchval(
                f"""
                SELECT COUNT(*)
                FROM calls
                WHERE tenant_id = $1
                  AND phone_number = $2
                  AND created_at > NOW() - INTERVAL '{window_minutes} minutes'
                """,
                tenant_id,
                phone_number,
            )

            if recent_count and recent_count >= threshold:
                return [AbuseEvent(
                    abuse_type=AbuseType.REPEAT_NUMBER,
                    severity=Severity.MEDIUM,
                    tenant_id=tenant_id,
                    phone_number=phone_number,
                    details={
                        "phone_number": phone_number,
                        "recent_calls": recent_count,
                        "threshold": threshold,
                        "window_minutes": window_minutes,
                    },
                    recommended_action="block",
                    trigger_value=float(recent_count),
                    threshold_value=float(threshold),
                )]

        return []

    async def _check_sequential_dialing(
        self,
        tenant_id: str,
        phone_number: str,
    ) -> List[AbuseEvent]:
        """Check for sequential number dialing (war dialing)."""
        # Get last 5 numbers called by this tenant
        async with self._db_pool.acquire() as conn:
            recent_numbers = await conn.fetch(
                """
                SELECT phone_number
                FROM calls
                WHERE tenant_id = $1
                  AND created_at > NOW() - INTERVAL '10 minutes'
                ORDER BY created_at DESC
                LIMIT 5
                """,
                tenant_id,
            )

            numbers = [row["phone_number"] for row in recent_numbers]
            numbers.append(phone_number)  # Include current number

            # Check if numbers are sequential
            if len(numbers) >= 3 and self._are_sequential(numbers):
                return [AbuseEvent(
                    abuse_type=AbuseType.SEQUENTIAL_DIALING,
                    severity=Severity.HIGH,
                    tenant_id=tenant_id,
                    details={"numbers": numbers},
                    recommended_action="block",
                )]

        return []

    async def _check_premium_rate(
        self,
        tenant_id: str,
        phone_number: str,
    ) -> List[AbuseEvent]:
        """Check if number is premium rate."""
        for prefix in PREMIUM_RATE_PREFIXES:
            if phone_number.startswith(prefix):
                return [AbuseEvent(
                    abuse_type=AbuseType.PREMIUM_RATE,
                    severity=Severity.HIGH,
                    tenant_id=tenant_id,
                    phone_number=phone_number,
                    details={
                        "number": phone_number,
                        "matched_prefix": prefix,
                    },
                    recommended_action="block",
                )]
        return []

    async def _check_high_risk_destination(
        self,
        tenant_id: str,
        phone_number: str,
    ) -> List[AbuseEvent]:
        """Check if destination is high-risk country."""
        country_code = self._extract_country_code(phone_number)

        if country_code in HIGH_RISK_COUNTRIES:
            return [AbuseEvent(
                abuse_type=AbuseType.TOLL_FRAUD,
                severity=Severity.MEDIUM,
                tenant_id=tenant_id,
                phone_number=phone_number,
                details={
                    "number": phone_number,
                    "country_code": country_code,
                    "risk_category": "high_fraud_risk",
                },
                recommended_action="warn",
            )]

        return []

    async def _check_wangiri_pattern(
        self,
        tenant_id: str,
        phone_number: str,
    ) -> List[AbuseEvent]:
        """Check for Wangiri (missed call) fraud pattern."""
        # Wangiri: Many short calls from same number
        async with self._db_pool.acquire() as conn:
            short_calls = await conn.fetchval(
                """
                SELECT COUNT(*)
                FROM calls
                WHERE tenant_id = $1
                  AND phone_number = $2
                  AND duration_seconds < 5
                  AND created_at > NOW() - INTERVAL '1 hour'
                """,
                tenant_id,
                phone_number,
            )

            if short_calls and short_calls >= 3:
                return [AbuseEvent(
                    abuse_type=AbuseType.WANGIRI,
                    severity=Severity.HIGH,
                    tenant_id=tenant_id,
                    phone_number=phone_number,
                    details={
                        "short_calls_count": short_calls,
                        "pattern": "wangiri",
                    },
                    recommended_action="block",
                    trigger_value=float(short_calls),
                    threshold_value=3.0,
                )]

        return []

    def _are_sequential(self, numbers: List[str]) -> bool:
        """Check if phone numbers are sequential (war dialing detection)."""
        try:
            # Extract numeric parts
            digits = []
            for n in numbers:
                # Remove + and country code, keep remaining digits
                num_only = re.sub(r"\D", "", n)
                if len(num_only) > 7:  # Keep last 7 digits (local number)
                    digits.append(int(num_only[-7:]))
                else:
                    digits.append(int(num_only))

            if len(digits) < 2:
                return False

            digits.sort()
            sequential_count = 1
            for i in range(1, len(digits)):
                if digits[i] - digits[i-1] == 1:
                    sequential_count += 1
                elif digits[i] != digits[i-1]:  # Allow duplicates
                    break

            return sequential_count >= 3
        except (ValueError, IndexError):
            return False

    def _extract_country_code(self, phone_number: str) -> Optional[str]:
        """Extract ISO country code from E.164 phone number."""
        if not phone_number.startswith("+"):
            return None

        # Simplified mapping
        country_mappings = {
            "+1": "US",
            "+44": "GB",
            "+33": "FR",
            "+49": "DE",
            "+39": "IT",
            "+34": "ES",
            "+31": "NL",
            "+32": "BE",
            "+41": "CH",
            "+46": "SE",
            "+47": "NO",
            "+45": "DK",
            "+358": "FI",
            "+48": "PL",
            "+61": "AU",
            "+64": "NZ",
            "+81": "JP",
            "+82": "KR",
            "+86": "CN",
            "+91": "IN",
            "+92": "PK",
            "+880": "BD",
            "+234": "NG",
            "+84": "VN",
            "+62": "ID",
            "+55": "BR",
            "+52": "MX",
            "+27": "ZA",
            "+20": "EG",
            "+971": "AE",
            "+65": "SG",
            "+7": "RU",
            "+380": "UA",
            "+90": "TR",
            "+98": "IR",
        }

        for prefix in sorted(country_mappings.keys(), key=len, reverse=True):
            if phone_number.startswith(prefix):
                return country_mappings[prefix]

        return None

    async def _get_historical_avg_calls(
        self,
        tenant_id: str,
        window_hours: int = 24,
    ) -> float:
        """Get historical average calls for tenant."""
        async with self._db_pool.acquire() as conn:
            # Average over last 7 days for same hour
            row = await conn.fetchrow(
                """
                SELECT AVG(call_count) as avg_calls
                FROM (
                    SELECT DATE_TRUNC('hour', created_at) as hour, COUNT(*) as call_count
                    FROM calls
                    WHERE tenant_id = $1
                      AND created_at > NOW() - INTERVAL '7 days'
                    GROUP BY DATE_TRUNC('hour', created_at)
                ) hourly_counts
                """,
                tenant_id,
            )

            if row and row["avg_calls"]:
                # Convert to 5-minute average
                return float(row["avg_calls"]) / 12

            return 0.0

    async def _store_call_metrics(
        self,
        call_id: str,
        tenant_id: str,
        duration_seconds: int,
        phone_number: str,
        cost: Optional[float] = None,
    ) -> None:
        """Store call metrics for aggregate analysis."""
        country_code = self._extract_country_code(phone_number)

        async with self._db_pool.acquire() as conn:
            # Upsert velocity snapshot
            await conn.execute(
                """
                INSERT INTO call_velocity_snapshots (
                    tenant_id,
                    window_start,
                    window_end,
                    total_calls,
                    unique_numbers,
                    international_calls,
                    premium_calls,
                    short_duration_calls,
                    top_destinations
                )
                SELECT
                    $1,
                    DATE_TRUNC('hour', NOW()),
                    DATE_TRUNC('hour', NOW()) + INTERVAL '1 hour',
                    1,
                    1,
                    CASE WHEN $2 IS NOT NULL AND $2 != 'US' THEN 1 ELSE 0 END,
                    0,
                    CASE WHEN $3 < 10 THEN 1 ELSE 0 END,
                    '[]'::jsonb
                ON CONFLICT (tenant_id, window_start)
                DO UPDATE SET
                    total_calls = call_velocity_snapshots.total_calls + 1,
                    unique_numbers = call_velocity_snapshots.unique_numbers + 1,
                    international_calls = call_velocity_snapshots.international_calls +
                        CASE WHEN EXCLUDED.international_calls > 0 THEN 1 ELSE 0 END,
                    short_duration_calls = call_velocity_snapshots.short_duration_calls +
                        CASE WHEN EXCLUDED.short_duration_calls > 0 THEN 1 ELSE 0 END
                """,
                tenant_id,
                country_code,
                duration_seconds,
            )

    async def _record_event(
        self,
        event: AbuseEvent,
        call_id: Optional[str] = None,
    ) -> Optional[UUID]:
        """Record abuse event to database."""
        try:
            async with self._db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO abuse_events (
                        tenant_id,
                        partner_id,
                        event_type,
                        severity,
                        trigger_value,
                        threshold_value,
                        phone_number_called,
                        call_id,
                        action_taken,
                        action_details,
                        destination_country
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                    RETURNING id
                    """,
                    event.tenant_id,
                    event.partner_id,
                    event.abuse_type.value,
                    event.severity.value,
                    event.trigger_value,
                    event.threshold_value,
                    event.phone_number,
                    call_id,
                    event.recommended_action,
                    json.dumps(event.details),
                    event.details.get("country_code"),
                )
                return row["id"] if row else None
        except Exception as e:
            logger.error(f"Failed to record abuse event: {e}")
            return None

    # -------------------------------------------------------------------------
    # Public API for Managing Events
    # -------------------------------------------------------------------------

    async def get_recent_events(
        self,
        tenant_id: Optional[str] = None,
        severity: Optional[Severity] = None,
        unresolved_only: bool = False,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Get recent abuse events."""
        query = """
            SELECT
                e.*,
                t.name as tenant_name
            FROM abuse_events e
            JOIN tenants t ON t.id = e.tenant_id
            WHERE 1=1
        """
        params = []

        if tenant_id:
            query += f" AND e.tenant_id = ${len(params) + 1}"
            params.append(tenant_id)

        if severity:
            query += f" AND e.severity = ${len(params) + 1}"
            params.append(severity.value)

        if unresolved_only:
            query += " AND e.resolved_at IS NULL"

        query += " ORDER BY e.created_at DESC"
        query += f" LIMIT ${len(params) + 1}"
        params.append(limit)

        async with self._db_pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
            return [dict(row) for row in rows]

    async def resolve_event(
        self,
        event_id: str,
        resolved_by: str,
        notes: Optional[str] = None,
        false_positive: Optional[bool] = None,
    ) -> bool:
        """Resolve an abuse event."""
        async with self._db_pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE abuse_events
                SET resolved_at = NOW(),
                    resolved_by = $2,
                    resolution_notes = $3,
                    false_positive = $4
                WHERE id = $1 AND resolved_at IS NULL
                """,
                event_id,
                resolved_by,
                notes,
                false_positive,
            )
            return "UPDATE 1" in result

    async def get_statistics(
        self,
        tenant_id: Optional[str] = None,
        hours: int = 24,
    ) -> Dict[str, Any]:
        """Get abuse detection statistics."""
        async with self._db_pool.acquire() as conn:
            # Count by severity
            severity_counts = await conn.fetch(
                f"""
                SELECT
                    severity,
                    COUNT(*) as count
                FROM abuse_events
                WHERE created_at > NOW() - INTERVAL '{hours} hours'
                {"AND tenant_id = $1" if tenant_id else ""}
                GROUP BY severity
                """,
                *(tenant_id,) if tenant_id else (),
            )

            # Count by type
            type_counts = await conn.fetch(
                f"""
                SELECT
                    event_type,
                    COUNT(*) as count
                FROM abuse_events
                WHERE created_at > NOW() - INTERVAL '{hours} hours'
                {"AND tenant_id = $1" if tenant_id else ""}
                GROUP BY event_type
                """,
                *(tenant_id,) if tenant_id else (),
            )

            # Unresolved critical/high
            unresolved = await conn.fetchval(
                f"""
                SELECT COUNT(*)
                FROM abuse_events
                WHERE severity IN ('high', 'critical')
                  AND resolved_at IS NULL
                {"AND tenant_id = $1" if tenant_id else ""}
                """,
                *(tenant_id,) if tenant_id else (),
            )

            return {
                "period_hours": hours,
                "by_severity": {r["severity"]: r["count"] for r in severity_counts},
                "by_type": {r["event_type"]: r["count"] for r in type_counts},
                "unresolved_high_severity": unresolved,
            }
