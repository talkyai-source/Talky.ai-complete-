"""Idempotent generate-and-store helper for call summaries.

Reads the transcript from the calls table, calls the summarizer, and
writes summary_json + headline back — all within a tenant-scoped
RLS-correct transaction.

Idempotency: reuse a summary only when its evidence revision matches and it
contains business details. Older/mismatched summaries are regenerated.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

from app.core.db_utils import acquire_with_tenant
from app.domain.services.transcript_service import conversation_turns, transcript_text_from_turns
from app.domain.services.call_summary.business_details import save_summary_details, transcript_revision, summary_snapshot
from app.domain.services.call_summary.summarizer import (
    SUMMARY_UNAVAILABLE_HEADLINE,
    summarize_transcript,
)

logger = logging.getLogger(__name__)


async def summary_response(pool, tenant_id: str, call_id: str, summary: Optional[dict]) -> dict:
    """Bind a response to one current durable source read after processing.

    Processing may outlive a transcript correction or a replacement analysis.
    Never return that superseded result as current, or equate saved partial
    evidence with a complete call. This read is an observation, not a lock on
    future revisions and not recovery of words that were never committed.
    """
    async with acquire_with_tenant(pool, tenant_id) as conn:
        row = await conn.fetchrow(
            "SELECT transcript, transcript_json, action_results, transcript_save_state, "
            "summary_json, summary_transcript_hash FROM calls "
            "WHERE id=$1::uuid AND tenant_id=$2::uuid",
            call_id, tenant_id,
        )
    revision = transcript_revision(row) if row is not None else None
    saved_summary = row.get("summary_json") if row is not None else None
    if isinstance(saved_summary, str):
        try:
            saved_summary = json.loads(saved_summary)
        except ValueError:
            saved_summary = None
    current = bool(
        isinstance(summary, dict) and isinstance(saved_summary, dict)
        and "business_details" in saved_summary and summary == saved_summary
        and row.get("summary_transcript_hash") == revision
    )
    save_state = row.get("transcript_save_state") if row is not None else None
    if save_state not in {"partial", "failed", "complete"}:
        save_state = "unknown"
    return {"available": current, "summary": summary if current else None,
            "source_evidence": {"transcript_save_state": save_state,
                "summary_current": current, "review_required": not current or save_state != "complete",
                "revision": revision}}


async def _confirmed_contacts_for_call(pool, tenant_id: str, call_id: str) -> dict[str, str]:
    """Confirmed contacts after the shared current-transcript evidence check.

    A stored row alone is not confirmation: unconfirmed values and failed
    revocations may both exist. Best-effort: a failed lookup supplies no facts.
    """
    try:
        from app.domain.services.lead_capture_service import LeadCaptureService

        rows = await LeadCaptureService(pool).details_for_call(tenant_id, call_id)
    except Exception as exc:  # noqa: BLE001 - a summary must never be blocked by this
        logger.warning(
            "call_summary store: confirmed-contact lookup failed for call %s: %s",
            call_id,
            exc,
        )
        return {}
    return {
        row["field_key"]: row["value"]
        for row in rows
        if row.get("field_key") in {"email", "phone"}
        and row.get("confirmed")
        and row.get("value")
    }


def _executed_actions_from_results(action_results: Any) -> list[str]:
    """Action names ``calls.action_results`` marks as actually succeeded."""
    if isinstance(action_results, str):
        try:
            action_results = json.loads(action_results)
        except json.JSONDecodeError:
            return []
    if not isinstance(action_results, dict):
        return []
    return [
        name
        for name, result in action_results.items()
        if isinstance(result, dict) and result.get("success")
    ]


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
        # action_results feeds the summarizer's ground-truth facts below (a5e033c7,
        # 2026-09-23: headline claimed "scheduled root canal appointment" while
        # action_results was `{}` — nothing had actually executed).
        row = await conn.fetchrow(
            "SELECT transcript, transcript_json, campaign_id, lead_id, summary_json, action_results, summary_transcript_hash FROM calls "
            "WHERE id = $1 AND tenant_id = $2::uuid",
            call_id,
            tenant_id,
        )

    if row is None:
        logger.warning("call_summary store: call %s not found for tenant %s", call_id, tenant_id)
        return None

    revision = transcript_revision(row)

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
        if (existing_dict is not None and "business_details" in existing_dict
                and row.get("summary_transcript_hash") == revision):
            # Self-heal: re-assert the lead flag from the existing summary.
            # Lead-marking shipped after some summaries already existed, and the
            # post-call generate path short-circuits here before reaching the
            # marker — so without this, historical qualified/callback calls never
            # flag their contact. Idempotent + best-effort (only writes leads when
            # the outcome is a lead and the contact isn't already flagged).
            await save_summary_details(pool, tenant_id, call_id, existing_dict, row)
            await refresh_latest_analysis(pool, tenant_id, call_id, existing_dict, revision, snapshot=summary_snapshot(row))
            await mark_lead_from_summary(pool, tenant_id, call_id, existing_dict,
                                         revision=revision, snapshot=summary_snapshot(row))
            return existing_dict

    # --- Transcript check ---
    stored_transcript = row["transcript"] or ""
    structured = row.get("transcript_json")
    if isinstance(structured, str):
        try:
            structured = json.loads(structured)
        except ValueError:
            structured = None
    structured_turns = structured.get("turns") if isinstance(structured, dict) else structured
    transcript_text = transcript_text_from_turns(structured_turns) if isinstance(structured_turns, list) and structured_turns else stored_transcript
    if not transcript_text.strip():
        logger.debug("call_summary store: call %s has no transcript — skipping", call_id)
        async with acquire_with_tenant(pool, tenant_id) as conn:
            await conn.execute(
                "UPDATE calls SET lead_details_status = 'no_transcript' WHERE id=$1::uuid AND tenant_id=$2::uuid AND transcript IS NOT DISTINCT FROM $3 "
                "AND jsonb_build_array(COALESCE(transcript,''),transcript_json,action_results)=$4::jsonb",
                call_id, tenant_id, row.get("transcript"), json.dumps(summary_snapshot(row), default=str),
            )
        return None

    # --- Generate ---
    # Ground the model in what this call actually established/executed so it
    # cannot state an unconfirmed contact or an unexecuted action as fact.
    confirmed_contacts = await _confirmed_contacts_for_call(pool, tenant_id, call_id)
    executed_actions = _executed_actions_from_results(row.get("action_results"))
    summary = await summarize_transcript(
        transcript_text,
        confirmed_contacts=confirmed_contacts,
        executed_actions=executed_actions,
    )

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
        async with acquire_with_tenant(pool, tenant_id) as conn:
            await conn.execute(
                "UPDATE calls SET lead_details_status = 'failed' WHERE id=$1::uuid AND tenant_id=$2::uuid AND transcript=$3 "
                "AND jsonb_build_array(COALESCE(transcript,''),transcript_json,action_results)=$4::jsonb",
                call_id, tenant_id, stored_transcript, json.dumps(summary_snapshot(row), default=str),
            )
        return summary

    # --- Persist (tenant-scoped) ---
    async with acquire_with_tenant(pool, tenant_id) as conn:
        written = await conn.execute(
            """
            UPDATE calls
               SET summary_json = $2::jsonb,
                   summary      = $3,
                   updated_at   = NOW(),
                   summary_transcript_hash = $6
             WHERE id = $1 AND tenant_id = $4::uuid AND transcript = $5
               AND transcript_json IS NOT DISTINCT FROM $7::jsonb
               AND action_results IS NOT DISTINCT FROM $8::jsonb
            """,
            call_id,
            json.dumps(summary),
            summary.get("headline", ""),
            tenant_id,
            stored_transcript,
            revision,
            _json_parameter(row.get("transcript_json")),
            _json_parameter(row.get("action_results")),
        )
        if written == "UPDATE 0":
            return None  # Transcript changed while analysis ran; retry the current revision.

    # The AI just judged the call — if it reads as a lead (goal achieved),
    # flag the contact green for follow-up. Best-effort; never blocks the
    # summary return.
    await save_summary_details(pool, tenant_id, call_id, summary, row)
    await refresh_latest_analysis(pool, tenant_id, call_id, summary, revision, snapshot=summary_snapshot(row))
    await mark_lead_from_summary(pool, tenant_id, call_id, summary,
                                 revision=revision, snapshot=summary_snapshot(row))

    return summary


def _json_parameter(value):
    return value if isinstance(value, str) or value is None else json.dumps(value)


async def refresh_latest_analysis(pool, tenant_id, call_id, summary, revision, *, snapshot=None) -> None:
    """Keep the newest call's analysis separate from the operator's note.

    A withdrawal/no-interest call updates this too; qualification is a separate
    decision below. Old jobs cannot replace newer-call information.
    """
    tips = summary.get("follow_up_tips") or []
    tip = tips[0].strip() if tips and isinstance(tips[0], str) else ""
    note = (tip or summary.get("next_step") or summary.get("headline") or "").strip()[:2000]
    async with acquire_with_tenant(pool, tenant_id) as conn:
        await conn.execute(
            """UPDATE leads l SET latest_analysis_note=$3, latest_analysis_call_id=c.id,
                       latest_analysis_at=c.created_at, updated_at=NOW()
                  FROM calls c WHERE c.id=$1::uuid AND c.tenant_id=$2::uuid
                    AND l.id=c.lead_id AND l.tenant_id=$2::uuid
                    AND c.summary_transcript_hash=$4
                    AND ($5::jsonb IS NULL OR jsonb_build_array(COALESCE(c.transcript,''),c.transcript_json,c.action_results)=$5::jsonb)
                    AND (l.latest_analysis_at IS NULL OR l.latest_analysis_at <= c.created_at)""",
            call_id, tenant_id, note, revision,
            json.dumps(snapshot, default=str) if snapshot is not None else None,
        )


# A "qualified lead" after a call the caller barely took part in is a false
# positive that reaches the client as a real-time alert (2026-09-07: "Qualified
# lead: … — how, we barely even talked"). Two independent gates below:
#   1. the summary itself must SUPPORT the label (qualification_status agrees,
#      and there is at least one concrete commitment / action / next step);
#   2. the conversation must have had substance: enough caller turns and
#      enough talk time. Thresholds are env-tunable but fail closed.
LEAD_MIN_CALLER_TURNS = int(os.getenv("LEAD_MIN_CALLER_TURNS", "3"))
LEAD_MIN_DURATION_S = int(os.getenv("LEAD_MIN_DURATION_S", "45"))


def _summary_supports_lead(summary: dict) -> tuple[bool, str]:
    """Gate 1 — does the summary's own evidence back the lead label?"""
    outcome = str(summary.get("outcome") or "").strip().lower()
    status = str(summary.get("qualification_status") or "").strip().lower()
    if outcome.startswith("qualified") and status and status != "qualified":
        return False, f"qualification_status={status}"
    if status == "unqualified":
        return False, "qualification_status=unqualified"
    commitments = [c for c in (summary.get("commitments") or []) if str(c).strip() and str(c).strip().lower() != "none"]
    actions = [a for a in (summary.get("action_items") or []) if a]
    next_step = str(summary.get("next_step") or "").strip()
    if not (commitments or actions or (next_step and next_step.lower() not in ("none", "unknown", "n/a"))):
        return False, "no_commitment_action_or_next_step"
    return True, "ok"


def _caller_turns(transcript_json: Any) -> int:
    turns = transcript_json
    if isinstance(turns, str):
        try:
            turns = json.loads(turns)
        except json.JSONDecodeError:
            return 0
    count = 0
    for turn in conversation_turns(turns):
        if not isinstance(turn, dict):
            continue
        # The transcript keeps every STT interim as its own row (is_final=False)
        # so one spoken sentence can appear 15 times while it is being
        # recognised. Only final recognitions are caller turns.
        if turn.get("is_final") is False or not turn.get("include_in_plaintext", True):
            continue
        role = str(turn.get("role") or turn.get("speaker") or "").lower()
        text = str(turn.get("content") or "").strip()
        if role in ("user", "caller", "customer") and len(text.split()) >= 2:
            count += 1
    return count


def _conversation_has_substance(row: Optional[dict]) -> tuple[bool, str]:
    """Gate 2 — was there enough of a conversation to qualify anyone?"""
    if not row:
        return False, "call_row_missing"
    turns = _caller_turns(row.get("transcript_json"))
    duration = row.get("duration_seconds")
    if turns < LEAD_MIN_CALLER_TURNS:
        return False, f"caller_turns={turns}<{LEAD_MIN_CALLER_TURNS}"
    if duration is not None and int(duration) < LEAD_MIN_DURATION_S:
        return False, f"duration_s={int(duration)}<{LEAD_MIN_DURATION_S}"
    return True, "ok"


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
    pool, tenant_id: str, call_id: str, summary: dict, *, revision: str, snapshot: list,
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
        supported, why = _summary_supports_lead(summary)
        if not supported:
            logger.info("lead_not_marked call=%s reason=summary:%s", call_id, why)
            return False
        # Prefer the first concrete follow-up tip; fall back to the next-step,
        # then the headline. follow_up_tips is a list (may be empty).
        tips = summary.get("follow_up_tips") or []
        first_tip = (tips[0].strip() if tips and isinstance(tips[0], str) else "")
        note = (first_tip or summary.get("next_step") or summary.get("headline") or "").strip()
        note = note or "Lead — please follow up."
        async with acquire_with_tenant(pool, tenant_id) as conn:
            call_row = await conn.fetchrow(
                "SELECT duration_seconds, transcript_json FROM calls "
                "WHERE id = $1 AND tenant_id = $2::uuid",
                call_id,
                tenant_id,
            )
            substance, why = _conversation_has_substance(dict(call_row) if call_row else None)
            if not substance:
                logger.info("lead_not_marked call=%s reason=conversation:%s", call_id, why)
                return False
            # RETURNING gives us the lead's identity so we can raise a real-time
            # alert with the name + number, not just silently flip the flag.
            row = await conn.fetchrow(
                """
                UPDATE leads AS l
                   SET is_lead          = true,
                       follow_up_note    = COALESCE(NULLIF(l.follow_up_note, ''), $2),
                       qualified_at      = NOW(),
                       qualified_call_id = $1,
                       updated_at        = NOW()
                  FROM calls AS c
                 WHERE c.id = $1
                   AND l.id = c.lead_id
                   AND l.is_lead = false
                   AND c.tenant_id = $3::uuid
                   AND l.tenant_id = $3::uuid
                   AND c.summary_transcript_hash=$4
                   AND jsonb_build_array(COALESCE(c.transcript,''),c.transcript_json,c.action_results)=$5::jsonb
                RETURNING l.id AS lead_id, l.first_name, l.last_name,
                          l.phone_number, l.campaign_id
                """,
                call_id,
                note,
                tenant_id,
                revision,
                json.dumps(snapshot, default=str) if snapshot is not None else None,
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
