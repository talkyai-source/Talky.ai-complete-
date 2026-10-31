"""Bounded, read-only evidence for an exact formal suspension event.

This is a projection of saved effects, never a second enforcement engine.
Caller evidence, current configuration and empty samples confer no proof.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException

from app.core.db_utils import acquire_with_tenant
from app.domain.services.call_status import TERMINAL_CALL_STATUSES
from app.domain.services.telephony.call_authority import (
    read_call_authority_rows,
    evaluate_call_authority,
)
from app.domain.services.telephony.control_backend_identity import saved_control_backend

EVIDENCE_KEY = "_formal_enforcement"
MAX_TENANTS = 200
MAX_SUBJECTS = 500
MAX_ROWS = 500
_LIVE_LEGS = "('initiated','ringing','answered','in_progress','in_call','active')"
_CALL_CANDIDATE = f"""(c.status <> ALL($2::text[])
 OR (c.provider_terminated_at IS NULL AND (
    (c.direction='inbound' AND c.billing_status='reserved')
    OR (c.direction='outbound' AND c.terminal_settled_at IS NULL)))
 OR EXISTS (SELECT 1 FROM call_legs l WHERE l.call_id=c.id AND l.status IN {_LIVE_LEGS}))"""
_CREDENTIALS = {
    "security_sessions": ("NOT revoked", "revoked"),
    "refresh_tokens": ("revoked_at IS NULL", "revoked_at IS NOT NULL"),
    "mfa_challenges": ("NOT used", "used"),
    "webauthn_challenges": ("NOT used", "used"),
}


def document(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def policy_digest(policy):
    # Restoration appends warnings but cannot rewrite the original hold policy.
    return (
        digest({k: v for k, v in policy.items() if k != "restoration_warnings"})
        if isinstance(policy, dict)
        else None
    )


def _cap(rows, limit=None):
    limit = MAX_ROWS if limit is None else limit
    return list(rows[:limit]), len(rows) <= limit


def _identity(row):
    return {
        "id": str(row["id"]),
        "tenant_id": str(row["tenant_id"]),
        "provider": row["provider"],
        "provider_call_id": row.get("provider_call_id") or row.get("external_call_uuid"),
        "control_backend": saved_control_backend(row.get("route_snapshot")),
    }


async def capture_cohort(
    conn, *, suspension_id, target_type, target_id, cutoff, tenant_ids, subject_ids, policy=None
):
    """Capture before credential revocation while the existing issuer fences hold.

    Optional inventory failure cannot prevent the security restriction. Its
    savepoint rolls back, and coverage remains explicitly unknown instead.
    """
    tenants, tenants_complete = _cap(sorted(set(str(v) for v in tenant_ids)), MAX_TENANTS)
    subjects, subjects_complete = _cap(sorted(set(str(v) for v in subject_ids)), MAX_SUBJECTS)
    cohort = {
        "tenant_ids": tenants,
        "subject_ids": subjects,
        "credentials": {},
        "calls": [],
        "runtime_ids": [],
        "coverage": {
            "subjects": subjects_complete and tenants_complete,
            "credentials": False,
            "calls": False,
            "runtimes": False,
        },
        "reasons": [],
    }
    envelope = {
        "version": 1,
        "event": {
            "suspension_id": str(suspension_id),
            "target_type": target_type,
            "target_id": str(target_id),
            "cutoff_db": cutoff.isoformat(),
        },
        "policy_sha256": policy_digest(policy),
        "cohort": cohort,
    }
    try:
        async with asyncio.timeout(3.0), conn.transaction():
            tables = list(_CREDENTIALS) if target_type == "user" else ["security_sessions"]
            complete = subjects_complete and tenants_complete
            for table in tables:
                rows = await conn.fetch(
                    f"SELECT id FROM {table} WHERE user_id=ANY($1::uuid[]) AND {_CREDENTIALS[table][0]} ORDER BY id LIMIT $2",
                    [UUID(v) for v in subjects],
                    MAX_ROWS + 1,
                )
                bounded, fits = _cap(rows)
                cohort["credentials"][table] = [str(row["id"]) for row in bounded]
                complete = complete and fits
                if not fits:
                    cohort["reasons"].append(f"{table}_capture_truncated")
            cohort["coverage"]["credentials"] = complete
            if target_type != "user":
                rows = await conn.fetch(
                    "SELECT c.id,c.tenant_id,c.provider,c.provider_call_id,c.external_call_uuid,c.route_snapshot "
                    f"FROM calls c WHERE c.tenant_id=ANY($1::uuid[]) AND {_CALL_CANDIDATE} ORDER BY c.id LIMIT $3",
                    [UUID(v) for v in tenants],
                    list(TERMINAL_CALL_STATUSES),
                    MAX_ROWS + 1,
                )
                bounded, fits = _cap(rows)
                cohort["calls"] = [_identity(row) for row in bounded]
                cohort["coverage"]["calls"] = fits and tenants_complete
                if not fits:
                    cohort["reasons"].append("call_capture_truncated")
            # Runtime registrations are additive evidence. Even a complete
            # query cannot establish deployment coverage across legacy pods.
            rows = await conn.fetch(
                "SELECT * FROM audit_logs WHERE action='assistant_runtime_registered' "
                "AND resource_type='assistant_runtime' AND metadata->>'user_id'=ANY($1::text[]) "
                "ORDER BY event_id LIMIT $2",
                subjects,
                MAX_ROWS + 1,
            )
            bounded, fits = _cap(rows)
            from app.core.security.assistant_runtime import stop_receipt_matches

            stopped = await conn.fetch(
                "SELECT * FROM audit_logs WHERE action='assistant_runtime_owner_stopped' "
                "AND resource_type='assistant_runtime' AND resource_id=ANY($1::uuid[]) ORDER BY event_id LIMIT $2",
                [row["resource_id"] for row in bounded],
                MAX_ROWS + 1,
            )
            stopped, stops_fit = _cap(stopped)
            cohort["runtime_ids"] = [
                str(row["resource_id"])
                for row in bounded
                if not any(
                    stop_receipt_matches(row, end)
                    and (at := _time(document(end["metadata"]).get("stopped_at"))) is not None
                    and at <= cutoff
                    for end in stopped
                )
            ]
            fits = fits and stops_fit
            cohort["runtime_inventory_complete"] = fits and subjects_complete
            if not fits:
                cohort["reasons"].append("runtime_capture_truncated")
            cohort["reasons"].append("runtime_deployment_coverage_unproven")
    except Exception as exc:
        cohort["coverage"] = {key: False for key in cohort["coverage"]}
        cohort["reasons"].append(f"capture_unavailable:{type(exc).__name__}")
    if not tenants_complete or not subjects_complete:
        cohort["reasons"].append("subject_or_tenant_capture_truncated")
    return envelope


def _dimension(key, *, coverage="unknown", status="unverified", reasons=None):
    return {
        "key": key,
        "status": status,
        "coverage": coverage,
        "counts": {
            k: 0 for k in ("captured", "observed", "confirmed", "pending", "exempt", "unknown")
        },
        "reasons": list(reasons or []),
    }


def _outcome(dimension):
    counts = dimension["counts"]
    if counts["pending"]:
        dimension["status"] = "pending"
    elif dimension["coverage"] != "complete" or counts["unknown"]:
        dimension["status"] = "unverified"
    elif counts["exempt"] and not counts["confirmed"]:
        dimension["status"] = "exempt_by_policy"
    else:
        dimension["status"] = "confirmed"
    return dimension


async def _original_proof(conn, event):
    saved = document(event.get("evidence")).get(EVIDENCE_KEY)
    expected_type = "user_suspended" if event["target_type"] == "user" else "tenant_suspended"

    def matches_original(row):
        return bool(
            row
            and row["event_type"] == expected_type
            and row["action"] == f"{event['target_type']}_suspended"
            and row["resource_type"] == event["target_type"]
            and row["resource_id"] == event["target_id"]
            and document(row["metadata"]).get("suspension_id") == str(event["suspension_id"])
        )

    original = None
    if isinstance(saved, dict):
        try:
            original = await conn.fetchrow(
                "SELECT * FROM audit_logs WHERE event_id=$1",
                UUID(str(saved.get("original_suspend_audit_id"))),
            )
        except (ValueError, TypeError):
            pass
    if not matches_original(original):
        original = await conn.fetchrow(
            "SELECT * FROM audit_logs WHERE action=$1 AND resource_type=$2 AND resource_id=$3 "
            "AND metadata->>'suspension_id'=$4 AND event_type=$5 ORDER BY event_time,event_id LIMIT 1",
            f"{event['target_type']}_suspended",
            event["target_type"],
            event["target_id"],
            str(event["suspension_id"]),
            expected_type,
        )
    if not matches_original(original):
        return None, None
    if not isinstance(saved, dict):
        return original, None
    candidate = {k: v for k, v in saved.items() if k != "original_suspend_audit_id"}
    metadata = document(original["metadata"])
    expected_event = {
        "suspension_id": str(event["suspension_id"]),
        "target_type": event["target_type"],
        "target_id": str(event["target_id"]),
        "cutoff_db": event["suspended_at"].isoformat(),
    }
    policy = (
        document(event.get("evidence")).get("_formal_account_hold")
        if event["target_type"] != "user"
        else None
    )
    valid = (
        type(saved.get("version")) is int
        and saved["version"] == 1
        and type(metadata.get("formal_enforcement_version")) is int
        and metadata["formal_enforcement_version"] == 1
        and original["action"] == f"{event['target_type']}_suspended"
        and original["resource_type"] == event["target_type"]
        and original["resource_id"] == event["target_id"]
        and metadata.get("suspension_id") == str(event["suspension_id"])
        and saved.get("event") == expected_event
        and saved.get("policy_sha256") == policy_digest(policy)
        and metadata.get("formal_enforcement_sha256") == digest(candidate)
    )
    return original, (
        saved.get("cohort") if valid and isinstance(saved.get("cohort"), dict) else None
    )


async def _credentials(conn, event, cohort, subjects, scope_complete, policy):
    result = _dimension("credentials")
    if event["target_type"] != "user" and policy and policy.get("immediate") is False:
        result.update(
            status="not_applicable",
            coverage="complete",
            reasons=["non_immediate_policy_does_not_revoke_credentials"],
        )
        return result
    tables = list(_CREDENTIALS) if event["target_type"] == "user" else ["security_sessions"]
    complete = bool(
        cohort and cohort.get("coverage", {}).get("credentials") is True and scope_complete
    )
    result["coverage"] = "complete" if complete else "unknown" if not cohort else "incomplete"
    for table in tables:
        ids = (cohort or {}).get("credentials", {}).get(table, [])
        result["counts"]["captured"] += len(ids)
        rows = await conn.fetch(
            f"SELECT id,{_CREDENTIALS[table][1]} AS invalidated FROM {table} "
            f"WHERE id=ANY($1::uuid[]) OR ($3 AND user_id=ANY($2::uuid[]) AND {_CREDENTIALS[table][0]}) ORDER BY id LIMIT $4",
            [UUID(v) for v in ids],
            [UUID(v) for v in subjects],
            event["is_active"],
            MAX_ROWS + 1,
        )
        rows, fits = _cap(rows)
        if not fits:
            result["coverage"] = "incomplete"
            result["reasons"].append(f"{table}_read_truncated")
        result["counts"]["observed"] += len(rows)
        result["counts"]["confirmed"] += sum(row["invalidated"] is True for row in rows)
        result["counts"]["pending"] += sum(row["invalidated"] is not True for row in rows)
        result["counts"]["unknown"] += len(set(ids) - {str(row["id"]) for row in rows})
    if not cohort:
        result["reasons"].append("original_credential_cohort_unproven")
    return _outcome(result)


def _time(value):
    try:
        result = (
            value
            if isinstance(value, datetime)
            else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        )
        return result if result.tzinfo is not None else None
    except (TypeError, ValueError):
        return None


async def _runtimes(conn, event, cohort, subjects):
    from app.core.security.assistant_runtime import stop_receipt_matches

    result = _dimension("assistant_runtimes", reasons=["runtime_deployment_coverage_unproven"])
    captured = (cohort or {}).get("runtime_ids", [])
    result["counts"]["captured"] = len(captured)
    rows = await conn.fetch(
        "SELECT * FROM audit_logs "
        "WHERE action='assistant_runtime_registered' AND event_type='session_created' "
        "AND resource_type='assistant_runtime' AND (resource_id=ANY($1::uuid[]) OR metadata->>'user_id'=ANY($2::text[])) "
        "ORDER BY event_id LIMIT $3",
        [UUID(v) for v in captured],
        subjects,
        MAX_ROWS + 1,
    )
    rows, fits = _cap(rows)
    if not fits:
        result["coverage"] = "incomplete"
        result["reasons"].append("runtime_inventory_truncated")
    # No inferred success from a process heartbeat, revoked credential, close
    # frame or elapsed watchdog period. Each saved open needs its exact stop.
    ids = [row["resource_id"] for row in rows]
    stops = await conn.fetch(
        "SELECT * FROM audit_logs WHERE action='assistant_runtime_owner_stopped' "
        "AND event_type='session_terminated' AND resource_type='assistant_runtime' "
        "AND resource_id=ANY($1::uuid[]) ORDER BY event_id LIMIT $2",
        ids,
        MAX_ROWS + 1,
    )
    stops, stops_fit = _cap(stops)
    if not stops_fit:
        result["coverage"] = "incomplete"
        result["reasons"].append("runtime_stop_inventory_truncated")
    seen = set()
    for row in rows:
        metadata = document(row["metadata"])
        opened = _time(metadata.get("registered_at"))
        if str(row["resource_id"]) not in captured and any(
            stop_receipt_matches(row, stop)
            and (at := _time(document(stop["metadata"]).get("stopped_at"))) is not None
            and at <= event["suspended_at"]
            for stop in stops
        ):
            continue
        if (
            not event["is_active"]
            and str(row["resource_id"]) not in captured
            and opened is not None
            and event["restored_at"] is not None
            and opened > event["restored_at"]
        ):
            continue  # A later legitimate runtime is outside this historical event.
        seen.add(str(row["resource_id"]))
        result["counts"]["observed"] += 1
        confirmed = metadata.get("user_id") in subjects and any(
            stop_receipt_matches(row, stopped)
            and (at := _time(document(stopped["metadata"]).get("stopped_at"))) is not None
            and at <= event["observed_at_db"]
            for stopped in stops
        )
        result["counts"]["confirmed" if confirmed else "unknown"] += 1
    result["counts"]["unknown"] += len(set(captured) - seen)
    return _outcome(result)


async def _calls(conn, event, cohort, tenant_ids, scope_complete, policies):
    result = _dimension("telephony")
    result["scope"] = "recorded_call_effects"
    if event["target_type"] == "user":
        result.update(
            status="not_applicable",
            coverage="complete",
            reasons=["staff_user_pstn_ownership_not_defined"],
        )
        return result
    from app.domain.services.telephony.lifecycle import _proven_call_started_at
    from app.domain.services.telephony.termination_receipts import receipt_matches

    captured = (cohort or {}).get("calls", [])
    captured_by_id = {item["id"]: item for item in captured}
    result["counts"]["captured"] = len(captured)
    rows = await conn.fetch(
        "SELECT c.* FROM calls c WHERE c.id=ANY($4::uuid[]) OR (c.tenant_id=ANY($1::uuid[]) AND ("
        f"{_CALL_CANDIDATE} OR c.created_at >= $3 OR c.answered_at >= $3 OR c.route_snapshot->'provider_acknowledgement' IS NOT NULL)) "
        "ORDER BY c.id LIMIT $5",
        [UUID(v) for v in tenant_ids],
        list(TERMINAL_CALL_STATUSES),
        event["suspended_at"],
        [UUID(v) for v in captured_by_id],
        MAX_ROWS + 1,
    )
    rows, fits = _cap(rows)
    complete = bool(
        cohort and cohort.get("coverage", {}).get("calls") is True and scope_complete and fits
    )
    result["coverage"] = "complete" if complete else "unknown" if not cohort else "incomplete"
    if not fits:
        result["reasons"].append("call_candidates_truncated")
    call_ids = [row["id"] for row in rows]
    legs = await conn.fetch(
        "SELECT id,call_id,provider,provider_leg_id,status,created_at FROM call_legs WHERE call_id=ANY($1::uuid[]) ORDER BY call_id,id LIMIT $2",
        call_ids,
        MAX_ROWS + 1,
    )
    legs, legs_fit = _cap(legs)
    proofs = await conn.fetch(
        "SELECT call_id,event_data FROM call_events WHERE call_id=ANY($1::uuid[]) AND event_type='admin_termination_confirmed' "
        "ORDER BY created_at DESC LIMIT $2",
        call_ids,
        MAX_ROWS + 1,
    )
    proofs, proofs_fit = _cap(proofs)
    if not legs_fit or not proofs_fit:
        result["coverage"] = "incomplete"
        result["reasons"].append("call_proof_inventory_truncated")
    seen = set()
    for raw in rows:
        row = dict(raw)
        call_id = str(row["id"])
        effect = _proven_call_started_at(row)
        current_legs = [dict(leg) for leg in legs if leg["call_id"] == row["id"]]
        potentially_live = (
            row["status"] not in TERMINAL_CALL_STATUSES
            or any(
                leg["status"]
                in {"initiated", "ringing", "answered", "in_progress", "in_call", "active"}
                for leg in current_legs
            )
            or (
                row.get("provider_terminated_at") is None
                and (
                    (row.get("direction") == "inbound" and row.get("billing_status") == "reserved")
                    or (
                        row.get("direction") == "outbound"
                        and row.get("terminal_settled_at") is None
                    )
                )
            )
        )
        within_event = (
            effect is not None
            and effect >= event["suspended_at"]
            and (
                event["is_active"] or event["restored_at"] is None or effect <= event["restored_at"]
            )
        )
        created = _time(row.get("created_at"))
        candidate_during_event = (
            created is not None
            and created >= event["suspended_at"]
            and (
                event["is_active"]
                or event["restored_at"] is None
                or created <= event["restored_at"]
            )
        )
        if (
            call_id not in captured_by_id
            and not within_event
            and not candidate_during_event
            and not (event["is_active"] and potentially_live)
        ):
            continue
        seen.add(call_id)
        result["counts"]["observed"] += 1
        # A saved original binding is immutable proof scope, not permission to
        # follow this ID onto a replacement provider/account.
        if call_id in captured_by_id and captured_by_id[call_id] != _identity(row):
            result["counts"]["unknown"] += 1
            result["reasons"].append("captured_call_identity_changed")
            continue
        confirmed = (
            legs_fit
            and proofs_fit
            and any(
                proof["call_id"] == row["id"]
                and (receipt := document(proof["event_data"])).get("version") == 3
                and receipt.get("inventory_coverage") == "complete_preproof"
                and (at := _time(receipt.get("confirmed_at"))) is not None
                and at <= event["observed_at_db"]
                and receipt_matches(row, current_legs, receipt)
                for proof in proofs
            )
        )
        if confirmed:
            result["counts"]["confirmed"] += 1
            continue
        if event["is_active"] and str(row["tenant_id"]) in policies:
            authority = evaluate_call_authority(
                policies[str(row["tenant_id"])],
                purpose="existing_call",
                existing_call_started_at=effect,
            )
            if authority.allowed:
                result["counts"]["exempt"] += 1
                continue
        if not event["is_active"]:
            original_policy = document(event.get("evidence")).get("_formal_account_hold", {})
            if original_policy.get("immediate") is False:
                result["counts"]["unknown"] += 1
                result["reasons"].append(
                    "historical_non_immediate_exemption_requires_original_authority"
                )
                continue
        # A live recorded effect is pending; a terminal label without full
        # inventory receipt remains unverified, never a proof of hangup.
        result["counts"]["pending" if potentially_live else "unknown"] += 1
    result["counts"]["unknown"] += len(set(captured_by_id) - seen)
    if not cohort:
        result["reasons"].append("original_call_cohort_unproven")
    if result["counts"]["unknown"]:
        result["reasons"].append("exact_v3_all_leg_proof_missing_or_invalid")
    result["reasons"] = sorted(set(result["reasons"]))
    return _outcome(result)


async def project_enforcement(pool, suspension_id):
    """One bounded repeatable-read snapshot. No mutation/provider requests."""
    try:
        async with asyncio.timeout(10.0), acquire_with_tenant(pool, None, timeout=3.0) as conn:
            # acquire_with_tenant issues SET LOCAL only before yielding; set
            # outer isolation before the first SELECT, never a nested level.
            await conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            await conn.execute("SET LOCAL statement_timeout = '3000ms'")
            observed = await conn.fetchval("SELECT clock_timestamp()")
            record = await conn.fetchrow(
                "SELECT e.*,a.metadata->'formal_account_hold_version'='1'::jsonb AS policy_audited "
                "FROM suspension_events e LEFT JOIN audit_logs a ON a.event_id=e.audit_log_id WHERE e.suspension_id=$1",
                UUID(str(suspension_id)),
            )
            if record is None:
                raise HTTPException(404, detail="Suspension not found")
            event = dict(record)
            event["observed_at_db"] = observed
            from app.domain.services.suspension_service import _account_policy

            policy = _account_policy(event)
            original, cohort = await _original_proof(conn, event)
            tenants = set((cohort or {}).get("tenant_ids", []))
            if event["target_type"] == "tenant":
                tenants.add(str(event["target_id"]))
            elif event["target_type"] == "partner":
                tenants.update((policy or {}).get("affected_tenant_ids", []))
                rows = await conn.fetch(
                    "SELECT id FROM tenants WHERE white_label_partner_id=$1 ORDER BY id LIMIT $2",
                    event["target_id"],
                    MAX_TENANTS + 1,
                )
                tenants.update(str(row["id"]) for row in rows)
            tenants, tenants_fit = _cap(sorted(tenants), MAX_TENANTS)
            subjects = set((cohort or {}).get("subject_ids", []))
            if event["target_type"] == "user":
                subjects.add(str(event["target_id"]))
            elif event["is_active"]:
                linked = await conn.fetch(
                    "SELECT DISTINCT user_id FROM tenant_users WHERE tenant_id=ANY($1::uuid[]) ORDER BY user_id LIMIT $2",
                    [UUID(v) for v in tenants],
                    MAX_SUBJECTS + 1,
                )
                subjects.update(str(row["user_id"]) for row in linked)
            subjects, subjects_fit = _cap(sorted(subjects), MAX_SUBJECTS)
            scope_complete = (
                tenants_fit
                and subjects_fit
                and bool(cohort and cohort.get("coverage", {}).get("subjects") is True)
            )
            if cohort and (
                set(tenants) - set(cohort.get("tenant_ids", []))
                or set(subjects) - set(cohort.get("subject_ids", []))
            ):
                scope_complete = False
            policies = await read_call_authority_rows(conn, tenants)
            authority_complete = tenants_fit and subjects_fit and len(policies) == len(tenants)
            remaining = set()
            restricted = False
            for row in policies.values():
                holds = row["active_holds"]
                holds = json.loads(holds) if isinstance(holds, str) else holds
                remaining.update(str(item["suspension_id"]) for item in holds)
                restricted = (
                    restricted or not evaluate_call_authority(row, purpose="principal").allowed
                )
            if event["target_type"] == "user":
                from app.core.security.principal import load_current_principal, PrincipalUnavailable

                try:
                    await load_current_principal(conn, event["target_id"])
                    restricted = False
                except PrincipalUnavailable:
                    restricted = True
                remaining = set()
                current_profile = await conn.fetchrow(
                    "SELECT tenant_id,role FROM user_profiles WHERE id=$1", event["target_id"]
                )
                if (
                    current_profile
                    and current_profile["role"] != "platform_admin"
                    and current_profile["tenant_id"]
                ):
                    current_rows = await read_call_authority_rows(
                        conn, [str(current_profile["tenant_id"])]
                    )
                    for row in current_rows.values():
                        holds = row["active_holds"]
                        holds = json.loads(holds) if isinstance(holds, str) else holds
                        remaining.update(str(item["suspension_id"]) for item in holds)
                authority_complete = (
                    True  # The actual principal read above is authoritative for this user.
                )
                remaining.update(
                    str(row["suspension_id"])
                    for row in await conn.fetch(
                        "SELECT suspension_id FROM suspension_events WHERE target_type='user' AND target_id=$1 AND is_active",
                        event["target_id"],
                    )
                )
            elif event["target_type"] == "partner":
                partner_status = await conn.fetchval(
                    "SELECT status FROM white_label_partners WHERE id=$1", event["target_id"]
                )
                restricted = restricted or partner_status != "active"
            direct_holds = await conn.fetch(
                "SELECT suspension_id FROM suspension_events WHERE target_type=$1 AND target_id=$2 AND is_active",
                event["target_type"],
                event["target_id"],
            )
            remaining.update(str(row["suspension_id"]) for row in direct_holds)
            restricted = restricted or bool(direct_holds)
            dimensions = [
                await _credentials(conn, event, cohort, subjects, scope_complete, policy),
                await _runtimes(conn, event, cohort, subjects),
                await _calls(conn, event, cohort, tenants, scope_complete, policies),
            ]
            all_complete = all(
                d["coverage"] == "complete"
                and d["status"] in {"confirmed", "exempt_by_policy", "not_applicable"}
                for d in dimensions
            )
            status = (
                "confirmed"
                if all_complete
                else (
                    "pending" if any(d["status"] == "pending" for d in dimensions) else "unverified"
                )
            )
            reasons = (
                list(cohort.get("reasons", []))
                if cohort
                else ["original_audited_cohort_unavailable"]
            )
            if not scope_complete:
                reasons.append("historical_expanded_or_truncated_scope")
            return {
                "version": 1,
                "suspension_id": event["suspension_id"],
                "target_type": event["target_type"],
                "target_id": event["target_id"],
                "event_state": "active" if event["is_active"] else "restored",
                "policy_cutoff": event["suspended_at"],
                "observed_at_db": observed,
                "original_audit_event_id": original["event_id"] if original else None,
                "current_authority": {
                    "status": (
                        "restricted"
                        if restricted
                        else "allowed" if authority_complete else "unknown"
                    ),
                    "remaining_hold_ids": sorted(remaining),
                },
                "propagation_status": status,
                "dimensions": dimensions,
                "reconciliation_reasons": reasons,
                "services_confirmed": [d["key"] for d in dimensions if d["status"] == "confirmed"],
                "pending_services": [
                    d["key"]
                    for d in dimensions
                    if d["status"] not in {"confirmed", "exempt_by_policy", "not_applicable"}
                ],
                "hold_active": event["is_active"],
                "audit_event_id": event["audit_log_id"],
                "detail": "Observed saved effects at one database snapshot; runtime deployment coverage is not proven.",
            }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, detail="Suspension enforcement evidence is unavailable.") from exc
