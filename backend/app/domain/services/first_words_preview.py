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


async def outbound_first_words(
    db_pool: Any, tenant_id: Any, *, agent_name: Optional[str], company_name: Optional[str],
) -> Optional[dict]:
    """{"notice_first": str|None, "opening": str, "notice_in_opening": bool}, or None.

    Never raises: any failure to read the policy logs one warning (exception type
    only) and returns None.
    """
    try:
        if db_pool is None or not tenant_id:
            raise RuntimeError("no database pool or tenant")
        from app.domain.services.notice_placement import placement_for
        from app.domain.services.recording_policy_service import (
            RecordingPolicyService,
            spoken_disclosure_text,
        )
        from app.domain.services.telephony_session_config import build_identity_opener

        # The callee's country is unknown in a preview: same as a call whose
        # callee cannot be placed (callee_country_code=None).
        decision = await RecordingPolicyService(db_pool).decide(
            tenant_id=str(tenant_id), callee_country_code=None,
        )
        text = spoken_disclosure_text(decision)
        placement = placement_for(decision, text, opener_available=True, pinned=False)
        opening = build_identity_opener(
            agent_name, company_name, None, recorded_line=placement.in_opening,
        )
        return {
            "notice_first": text if (text and not placement.in_opening) else None,
            "opening": opening,
            "notice_in_opening": bool(placement.in_opening),
        }
    except Exception as exc:
        logger.warning("first_words_preview_unavailable error=%s", type(exc).__name__)
        return None
