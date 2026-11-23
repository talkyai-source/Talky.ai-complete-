"""What an outgoing recorded call of this account says first, for the campaign preview.

Uses the same functions the call uses (`RecordingPolicyService.decide`,
`spoken_disclosure_text`, `placement_for`, `build_identity_opener`) so the
preview cannot drift from the call. Read-only: no SQL of its own, no
`recording_notice_placement` log line (that line is evidence about real calls) and
no entry in the per-call notice ledger (the preview has no call id).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# The reason `RecordingPolicyService.decide` sets when a one-party account owes a
# notice only because the callee's country is unknown or needs one
# (recording_policy_service.py, the one-party branch); a test pins it to the real decide.
_ONE_PARTY_PLACE_REASON = "tenant_policy_one_party_two_party_destination"


class _PolicyLookupFailed(Exception):
    pass


async def outbound_first_words(
    db_pool: Any, tenant_id: Any, *, agent_name: Optional[str], company_name: Optional[str],
    opening_mode: Optional[str] = None,
) -> Optional[dict]:
    """The first words of an outgoing call of this account, or None.

    Keys: notice_first, opening, notice_in_opening, waits_for_person (the
    campaign waits for the person's first words), notice_alone (the sentence(s)
    said when the person's first words are not a greeting, so no opening is said:
    always tells them it is an AI), notice_depends_on_place (a notice is owed only because
    the callee's place is unknown).

    Never raises: any failure to read the policy logs one warning (exception type
    only) and returns None.
    """
    try:
        if db_pool is None or not tenant_id:
            raise RuntimeError("no database pool or tenant")
        from app.domain.services.notice_placement import placement_for
        from app.domain.services.recording_policy_service import (
            POLICY_LOOKUP_FAILED_REASON,
            RecordingPolicyService,
            spoken_disclosure_text,
        )
        from app.domain.services.telephony_session_config import build_identity_opener
        from app.domain.services.voice_pipeline.first_line_identity import identity_line

        # The callee's country is unknown in a preview: same as a call whose
        # callee cannot be placed (callee_country_code=None).
        decision = await RecordingPolicyService(db_pool).decide(
            tenant_id=str(tenant_id), callee_country_code=None,
        )
        if getattr(decision, "reason", None) == POLICY_LOOKUP_FAILED_REASON:
            # decide() does not raise on a failed read: it returns the platform
            # default. That is what a call says, but it says nothing about this
            # account's real policy, so the preview shows nothing.
            raise _PolicyLookupFailed()
        text = spoken_disclosure_text(decision)
        placement = placement_for(decision, text, opener_available=True, pinned=False)
        opening = build_identity_opener(
            agent_name, company_name, None, recorded_line=placement.in_opening,
        )
        notice_first = text if (text and not placement.in_opening) else None
        # What really follows first words that are not a greeting (the same
        # function the call uses): the one sentence with the notice words in it
        # (the platform's own wording), or the account's own notice sentence and
        # then the plain sentence, or the plain sentence alone when no notice is owed.
        in_line = placement_for(
            decision, text, opener_available=True, pinned=False, carrier="identity_line",
        ).in_opening
        plain = identity_line(agent_name, company_name, recorded_line=False)
        if in_line:
            alone = identity_line(agent_name, company_name, recorded_line=True)
        elif text:
            alone = f"{text} {plain}"
        else:
            alone = plain
        waits = opening_mode == "callee_first"
        logger.info(
            "first_words_preview tenant=%s notice=%s waits_for_person=%s",
            str(tenant_id)[:8],
            "in_opening" if placement.in_opening else ("first" if notice_first else "none"),
            "true" if waits else "false",
        )
        return {
            "notice_first": notice_first,
            "opening": opening,
            "notice_in_opening": bool(placement.in_opening),
            "waits_for_person": waits,
            "notice_alone": alone,
            "notice_depends_on_place": bool(text) and decision.reason == _ONE_PARTY_PLACE_REASON,
        }
    except _PolicyLookupFailed:
        logger.warning("first_words_preview_unavailable error=policy_lookup_failed")
        return None
    except Exception as exc:
        logger.warning("first_words_preview_unavailable error=%s", type(exc).__name__)
        return None
