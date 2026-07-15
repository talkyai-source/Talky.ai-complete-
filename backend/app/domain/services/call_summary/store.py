"""Idempotent generate-and-store helper for call summaries.

Reads the transcript from the calls table, calls the summarizer, and
writes summary_json + headline back — all within a tenant-scoped
RLS-correct transaction.

Idempotency: if summary_json is already populated and force=False, the
existing value is returned immediately without re-summarizing.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from app.core.db_utils import acquire_with_tenant
from app.domain.services.call_summary.summarizer import (
    SUMMARY_UNAVAILABLE_HEADLINE,
    summarize_transcript,
)

logger = logging.getLogger(__name__)


async def generate_and_store(
    pool,
    tenant_id: str,
    call_id: str,
    *,
    force: bool = False,
) -> Optional[dict]:
    """Generate (if needed) and persist a structured call summary.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    tenant_id:
        UUID string of the owning tenant (used to set RLS context).
    call_id:
        UUID string of the call row to summarize.
    force:
        When True, re-summarize even if summary_json is already set.

    Returns
    -------
    dict | None
        The summary dict if a summary was generated or already existed,
        or None when the call row is missing or has no transcript.
    """
    async with acquire_with_tenant(pool, tenant_id) as conn:
        # Defense-in-depth: `acquire_with_tenant` already sets the RLS GUC and
        # `calls` has a tenant-isolation policy, but pin the predicate here too
        # so object-level scoping holds even if RLS were ever disabled/misset.
        row = await conn.fetchrow(
            "SELECT transcript, summary_json FROM calls "
            "WHERE id = $1 AND tenant_id = $2::uuid",
            call_id,
            tenant_id,
        )

    if row is None:
        logger.warning("call_summary store: call %s not found for tenant %s", call_id, tenant_id)
        return None

    # --- Idempotency check ---
    existing = row["summary_json"]
    if existing is not None and not force:
        # asyncpg may return JSONB as a str or as a dict depending on
        # whether a codec is registered.  Handle both shapes.
        existing_dict: Optional[dict] = None
        if isinstance(existing, str):
            try:
                existing_dict = json.loads(existing)
            except json.JSONDecodeError:
                logger.warning(
                    "call_summary store: summary_json for call %s is invalid JSON — re-generating",
                    call_id,
                )
        else:
            existing_dict = dict(existing)
        if existing_dict is not None:
            # Self-heal: re-assert the lead flag from the existing summary.
            # Lead-marking shipped after some summaries already existed, and the
            # post-call generate path short-circuits here before reaching the
            # marker — so without this, historical qualified/callback calls never
            # flag their contact. Idempotent + best-effort (only writes leads when
            # the outcome is a lead and the contact isn't already flagged).
            await mark_lead_from_summary(pool, tenant_id, call_id, existing_dict)
            return existing_dict

    # --- Transcript check ---
    transcript_text: str = row["transcript"] or ""
    if not transcript_text.strip():
        logger.debug("call_summary store: call %s has no transcript — skipping", call_id)
        return None

    # --- Generate ---
    summary = await summarize_transcript(transcript_text)

    # A fail-soft summarizer error (network/SDK/429, or output that won't parse
    # as JSON) returns the "Summary unavailable" sentinel. Persisting it would
    # poison the row: the idempotency check above would then skip this call
    # forever, leaving it permanently stuck on "Summary unavailable". Return it
    # WITHOUT persisting so the next view / backfill retries.
    if summary.get("headline") == SUMMARY_UNAVAILABLE_HEADLINE:
        logger.warning(
            "call_summary store: summarizer failed for call %s (tenant %s) — "
            "not persisting so it can be retried later",
            call_id,
            tenant_id,
        )
        return summary

    # --- Persist (tenant-scoped) ---
    async with acquire_with_tenant(pool, tenant_id) as conn:
        await conn.execute(
            """
            UPDATE calls
               SET summary_json = $2::jsonb,
                   summary      = $3,
                   updated_at   = NOW()
             WHERE id = $1 AND tenant_id = $4::uuid
            """,
            call_id,
            json.dumps(summary),
            summary.get("headline", ""),
            tenant_id,
        )

    # The AI just judged the call — if it reads as a lead (goal achieved),
    # flag the contact green for follow-up. Best-effort; never blocks the
    # summary return.
    await mark_lead_from_summary(pool, tenant_id, call_id, summary)

    return summary


def _outcome_is_lead(outcome: str) -> bool:
    """True when the AI's outcome label means "this is a lead / goal achieved".

    The summarizer's ``outcome`` is a free-text label that STARTS with one of:
    qualified | disqualified | callback | no_interest | voicemail | error.
    We treat ``qualified`` and ``callback`` as leads worth following up.
    ``startswith("qualified")`` deliberately excludes ``disqualified`` (it
    starts with "dis").
    """
    o = (outcome or "").strip().lower()
    return o.startswith("qualified") or o.startswith("callback")


def _lead_display_name(row: dict) -> str:
    name = " ".join(p for p in [(row.get("first_name") or "").strip(), (row.get("last_name") or "").strip()] if p).strip()
    return name or (row.get("phone_number") or "New lead")


async def _emit_qualified_lead_alert(conn, tenant_id: str, call_id: str, row: dict, note: str) -> None:
    """Write a qualified-lead row to the Event Stream (best-effort, never raises).

    Runs on the SAME tenant-scoped connection as the qualify UPDATE so the
    stream_events INSERT is under the correct RLS context.
    """
    try:
        from app.domain.services.event_emitter import emit_event

        name = _lead_display_name(row)
        phone = (row.get("phone_number") or "").strip()
        campaign_id = row.get("campaign_id")
        title = f"Qualified lead: {name}" + (f" · {phone}" if phone else "")
        await emit_event(
            conn,
            tenant_id=tenant_id,
            category="alert",
            severity="info",
            title=title,
            description=note,
            related_campaign_id=str(campaign_id) if campaign_id else None,
            related_call_id=call_id,
            metadata={
                "kind": "qualified_lead",
                "lead_id": str(row.get("lead_id")) if row.get("lead_id") else None,
                "name": name,
                "phone_number": phone or None,
                "follow_up_note": note,
                "campaign_id": str(campaign_id) if campaign_id else None,
            },
        )
    except Exception as exc:  # noqa: BLE001 — alerting must never break qualification
        logger.warning("qualified_lead alert emit failed for call %s: %s", call_id, exc)


async def mark_lead_from_summary(
    pool, tenant_id: str, call_id: str, summary: dict
) -> bool:
    """Flag the call's contact as a lead when the AI summary says so.

    Sets ``leads.is_lead`` + a short ``follow_up_note`` (the AI's next-step /
    headline) on the contact linked to this call. Best-effort: logs and returns
    False on any error so it never breaks post-call processing. Returns True if
    a contact was flagged.
    """
    try:
        if not _outcome_is_lead(str(summary.get("outcome") or "")):
            return False
        # Prefer the first concrete follow-up tip; fall back to the next-step,
        # then the headline. follow_up_tips is a list (may be empty).
        tips = summary.get("follow_up_tips") or []
        first_tip = (tips[0].strip() if tips and isinstance(tips[0], str) else "")
        note = (first_tip or summary.get("next_step") or summary.get("headline") or "").strip()
        note = note or "Lead — please follow up."
        async with acquire_with_tenant(pool, tenant_id) as conn:
            # RETURNING gives us the lead's identity so we can raise a real-time
            # alert with the name + number, not just silently flip the flag.
            row = await conn.fetchrow(
                """
                UPDATE leads AS l
                   SET is_lead          = true,
                       follow_up_note    = $2,
                       qualified_at      = NOW(),
                       qualified_call_id = $1,
                       updated_at        = NOW()
                  FROM calls AS c
                 WHERE c.id = $1
                   AND l.id = c.lead_id
                   AND l.is_lead = false
                   AND c.tenant_id = $3::uuid
                   AND l.tenant_id = $3::uuid
                RETURNING l.id AS lead_id, l.first_name, l.last_name,
                          l.phone_number, l.campaign_id
                """,
                call_id,
                note,
                tenant_id,
            )
            flagged = row is not None
            if flagged:
                # Alert the client in real time (Event Stream, already polled by
                # the dashboard) so a qualified lead during an active campaign is
                # surfaced immediately with the contact's name + number — instead
                # of the flag sitting unseen until someone opens Contacts.
                await _emit_qualified_lead_alert(conn, tenant_id, call_id, dict(row), note)
        if flagged:
            logger.info("lead_marked call=%s tenant=%s note=%r", call_id, tenant_id, note)
        return flagged
    except Exception as exc:  # noqa: BLE001 — never break post-call processing
        logger.warning("mark_lead_from_summary failed for call %s: %s", call_id, exc)
        return False
