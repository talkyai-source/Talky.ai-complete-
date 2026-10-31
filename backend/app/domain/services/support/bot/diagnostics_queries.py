"""Read-only SQL for the support bot tools.

Every statement is tenant-scoped with an explicit ``tenant_id`` predicate
(``tenants`` itself is keyed by ``id``). Never select transcripts, recording
URLs, summaries, connector ``config`` or full phone numbers.
"""
from __future__ import annotations

from typing import Any

TENANT_SQL = "SELECT plan_id, subscription_status, status FROM tenants WHERE id = $1"
PLAN_SQL = "SELECT name, price FROM plans WHERE id = $1"

CAMPAIGN_BY_ID_SQL = (
    "SELECT id, name, status, direction, updated_at FROM campaigns "
    "WHERE id = $1 AND tenant_id = $2"
)
CAMPAIGN_BY_NAME_SQL = (
    "SELECT id, name, status, direction, updated_at FROM campaigns "
    "WHERE lower(name) = lower($1) AND tenant_id = $2 ORDER BY updated_at DESC LIMIT 1"
)
CAMPAIGN_FAILURES_SQL = (
    "SELECT failure_reason, max(failure_category) AS failure_category, count(*) AS n "
    "FROM dialer_jobs WHERE campaign_id = $1 AND tenant_id = $2 AND status = 'failed' "
    "AND updated_at > now() - interval '24 hours' "
    "GROUP BY failure_reason ORDER BY n DESC LIMIT 5"
)
RECENT_FAILURES_SQL = (
    "SELECT failure_reason, max(failure_category) AS failure_category, count(*) AS n "
    "FROM dialer_jobs WHERE tenant_id = $1 "
    "AND (status = 'failed' OR failure_reason IS NOT NULL) "
    "AND updated_at > now() - interval '24 hours' "
    "GROUP BY failure_reason ORDER BY n DESC LIMIT 5"
)
CALL_SQL = (
    "SELECT id, campaign_id, status, outcome, direction, started_at, ended_at, "
    "duration_seconds, dialer_job_id FROM calls WHERE id = $1 AND tenant_id = $2"
)
JOB_REASON_SQL = (
    "SELECT failure_reason, failure_category FROM dialer_jobs WHERE id = $1 AND tenant_id = $2"
)
PHONES_SQL = (
    "SELECT e164, status, verified_at, provider FROM tenant_phone_numbers "
    "WHERE tenant_id = $1 ORDER BY e164 LIMIT 20"
)
CONNECTORS_SQL = (
    "SELECT type, provider, name, status, provider_revocation_status FROM connectors "
    "WHERE tenant_id = $1 ORDER BY name LIMIT 20"
)
TICKETS_SQL = (
    "SELECT id, number, subject, status, category FROM support_tickets "
    "WHERE tenant_id = $1 AND requester_user_id = $2 "
    "AND status IN ('open','in_progress','waiting_on_customer') "
    "ORDER BY updated_at DESC LIMIT 5"
)


def mask_phone(value: Any) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return f"***{digits[-4:]}" if digits else "***"


def iso(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else (None if value is None else str(value))
