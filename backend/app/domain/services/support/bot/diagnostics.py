"""Read-only diagnostic tools for the support bot.

The tenant and user come from the executor, never from model arguments. A
foreign or unknown id answers ``not_found`` and reveals nothing.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from app.core.db_utils import acquire_with_tenant
from app.domain.services.call_issue_advice import advise
from app.domain.services.dialer.block_state import read_block_reason
from app.domain.services.minutes_quota import compute_minutes_status
from app.domain.services.support import vocabulary
from app.domain.services.support.bot import diagnostics_queries as q
from app.domain.services.support.bot.tools import validate_tool_call
from app.domain.services.support.bot.types import Finding, Source, ToolTrace

logger = logging.getLogger(__name__)

STAFF_ONLY_BLOCKS = frozenset({"caller_id_not_verified", "line_not_ready"})
_BILLABLE_SUBSCRIPTIONS = ("active", "past_due")
_OK_SUBSCRIPTIONS = ("active", "trialing", "past_due")
_SNIPPET_MAX = 200


@dataclass(frozen=True)
class ToolOutcome:
    result: dict[str, Any]
    trace: ToolTrace
    findings: tuple[Finding, ...] = ()
    sources: tuple[Source, ...] = ()
    account: dict[str, Any] | None = None


@dataclass
class _Part:
    result: dict[str, Any]
    summary: str
    findings: list[Finding] = field(default_factory=list)
    sources: list[Source] = field(default_factory=list)
    account: dict[str, Any] | None = None


def _outcome(name: str, result: dict[str, Any], summary: str, **kw: Any) -> ToolOutcome:
    return ToolOutcome(result, ToolTrace(name, summary[:200]), **kw)


def _error(name: str, code: str) -> ToolOutcome:
    return _outcome(name, {"error": code}, f"{code}")


def _group(rows: list[Any]) -> tuple[list[dict[str, Any]], list[Finding]]:
    groups: list[dict[str, Any]] = []
    findings: list[Finding] = []
    for row in rows:
        reason = row["failure_reason"] or "unknown"
        advice = advise(row["failure_reason"], category=row["failure_category"])
        groups.append({"reason": str(reason)[:80], "count": int(row["n"]), "title": advice.title,
                       "suggestion": advice.suggestion, "severity": advice.severity})
        if advice.severity == "error":
            findings.append(Finding(code=str(reason)[:80], title=advice.title, detail=advice.suggestion,
                                    staff_only=str(reason) in STAFF_ONLY_BLOCKS))
    return groups, findings


class ToolExecutor:
    def __init__(self, *, pool: Any, redis: Any, tenant_id: str, user_id: str) -> None:
        self._pool = pool
        self._redis = redis
        self._tenant = str(tenant_id)
        self._user = str(user_id)

    async def execute(self, name: str, raw_arguments: str | dict | None) -> ToolOutcome:
        args, err = validate_tool_call(name, raw_arguments)
        if err or args is None:
            return _error(name, err or "invalid_arguments")
        try:
            part = await getattr(self, f"_{name}")(args)
        except Exception as exc:  # noqa: BLE001 - log the type only
            logger.warning("support_bot_tool_failed tool=%s error=%s", name, type(exc).__name__)
            return _outcome(name, {"error": "check_unavailable"}, "check unavailable")
        if part is None:
            return _error(name, "not_found")
        return _outcome(name, part.result, part.summary, findings=tuple(part.findings),
                        sources=tuple(part.sources), account=part.account)

    async def _search_help_articles(self, args: Any) -> _Part:
        from app.domain.services.support.articles import search_articles

        hits = await search_articles(self._pool, args.query, limit=3, tenant_id=self._tenant)
        sources = [Source(h["slug"], h["title"], h["url"]) for h in hits]
        shown = [{"slug": h["slug"], "title": h["title"], "section": h.get("section"),
                  "snippet": str(h.get("snippet") or "")[:_SNIPPET_MAX]} for h in hits]
        return _Part({"articles": shown}, f"searched help articles, {len(hits)} found", sources=sources)

    async def _get_account_overview(self, args: Any) -> _Part | None:
        async with acquire_with_tenant(self._pool, self._tenant) as conn:
            tenant = await conn.fetchrow(q.TENANT_SQL, self._tenant)
            if tenant is None:
                return None
            plan = await conn.fetchrow(q.PLAN_SQL, tenant["plan_id"]) if tenant["plan_id"] else None
            minutes = await compute_minutes_status(conn, self._tenant)
        sub, status = tenant["subscription_status"], tenant["status"]
        price = plan["price"] if plan else 0
        paying = sub in _BILLABLE_SUBSCRIPTIONS and bool(price and price > 0)
        account = {"paying": paying, "plan": plan["name"] if plan else None,
                   "subscription_status": sub, "account_status": status}
        findings: list[Finding] = []
        if status == "suspended":
            findings.append(Finding("account_suspended", "Account suspended",
                                    "The account is suspended; only the support team can lift this.",
                                    staff_only=True, account_wide_block=True))
        if minutes.state == "known" and minutes.exhausted:
            advice = advise("out_of_minutes")
            findings.append(Finding("out_of_minutes", advice.title, advice.suggestion, account_wide_block=True))
        if sub not in _OK_SUBSCRIPTIONS:
            findings.append(Finding("subscription_inactive", "Subscription is not active",
                                    "Calling is blocked until the subscription is active again.",
                                    account_wide_block=True))
        return _Part({**account, "minutes": minutes.as_dict()}, "checked plan, subscription and minutes",
                     findings=findings, account=account)

    async def _get_campaign_status(self, args: Any) -> _Part | None:
        ref = args.campaign.strip()
        async with acquire_with_tenant(self._pool, self._tenant) as conn:
            row = None
            try:
                row = await conn.fetchrow(q.CAMPAIGN_BY_ID_SQL, str(UUID(ref)), self._tenant)
            except ValueError:
                row = await conn.fetchrow(q.CAMPAIGN_BY_NAME_SQL, ref, self._tenant)
            if row is None:
                return None
            cid = str(row["id"])
            failures = await conn.fetch(q.CAMPAIGN_FAILURES_SQL, cid, self._tenant)
        result: dict[str, Any] = {"name": row["name"], "status": row["status"],
                                  "direction": row["direction"], "updated_at": q.iso(row["updated_at"])}
        findings: list[Finding] = []
        block = await read_block_reason(self._redis, cid)
        if block and block.get("code"):
            code = str(block["code"])
            advice = advise(code)
            result["current_block"] = {"code": code, "title": advice.title, "suggestion": advice.suggestion}
            findings.append(Finding(code, advice.title, advice.suggestion, staff_only=code in STAFF_ONLY_BLOCKS,
                                    related_campaign_id=cid))
        groups, _ = _group(list(failures))
        result["failures_24h"] = groups
        return _Part(result, f"checked campaign status ({row['status']})", findings=findings)

    async def _get_recent_call_issues(self, args: Any) -> _Part:
        async with acquire_with_tenant(self._pool, self._tenant) as conn:
            rows = await conn.fetch(q.RECENT_FAILURES_SQL, self._tenant)
        groups, findings = _group(list(rows))
        return _Part({"issues_24h": groups}, f"checked failed calls in the last 24h, {len(groups)} kinds",
                     findings=findings)

    async def _get_call_details(self, args: Any) -> _Part | None:
        async with acquire_with_tenant(self._pool, self._tenant) as conn:
            row = await conn.fetchrow(q.CALL_SQL, str(args.call_id), self._tenant)
            if row is None:
                return None
            job = None
            if row["dialer_job_id"]:
                job = await conn.fetchrow(q.JOB_REASON_SQL, str(row["dialer_job_id"]), self._tenant)
        result: dict[str, Any] = {
            "status": row["status"], "outcome": row["outcome"], "direction": row["direction"],
            "started_at": q.iso(row["started_at"]), "ended_at": q.iso(row["ended_at"]),
            "duration_seconds": row["duration_seconds"]}
        findings: list[Finding] = []
        if job and job["failure_reason"]:
            advice = advise(job["failure_reason"], category=job["failure_category"])
            result["failure"] = {"reason": str(job["failure_reason"])[:80], "title": advice.title,
                                 "suggestion": advice.suggestion}
            if advice.severity == "error":
                findings.append(Finding(str(job["failure_reason"])[:80], advice.title, advice.suggestion,
                                        related_call_id=str(args.call_id)))
        return _Part(result, "checked one call", findings=findings)

    async def _get_phone_numbers_status(self, args: Any) -> _Part:
        async with acquire_with_tenant(self._pool, self._tenant) as conn:
            rows = await conn.fetch(q.PHONES_SQL, self._tenant)
        numbers = [{"number": q.mask_phone(r["e164"]), "status": r["status"], "provider": r["provider"],
                    "verified_at": q.iso(r["verified_at"])} for r in rows]
        findings: list[Finding] = []
        if any(r["status"] != "verified" for r in rows):
            advice = advise("caller_id_not_verified")
            findings.append(Finding("caller_id_not_verified", advice.title, advice.suggestion, staff_only=True))
        return _Part({"numbers": numbers}, f"checked {len(numbers)} phone numbers", findings=findings)

    async def _get_connectors_status(self, args: Any) -> _Part:
        async with acquire_with_tenant(self._pool, self._tenant) as conn:
            rows = await conn.fetch(q.CONNECTORS_SQL, self._tenant)
        items = [{"type": r["type"], "provider": r["provider"], "name": r["name"], "status": r["status"],
                  "revocation": r["provider_revocation_status"]} for r in rows]
        return _Part({"connectors": items}, f"checked {len(items)} connectors")

    async def _find_open_tickets(self, args: Any) -> _Part:
        async with acquire_with_tenant(self._pool, self._tenant) as conn:
            rows = await conn.fetch(q.TICKETS_SQL, self._tenant, self._user)
        items = [{"number": vocabulary.ticket_display_number(r["number"]), "subject": str(r["subject"])[:120],
                  "status": r["status"], "category": r["category"]} for r in rows]
        return _Part({"tickets": items}, f"found {len(items)} open tickets")


def result_json(outcome: ToolOutcome) -> str:
    return json.dumps(outcome.result, default=str, separators=(",", ":"))
