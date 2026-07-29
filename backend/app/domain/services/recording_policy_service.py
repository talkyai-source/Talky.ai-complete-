"""Per-tenant recording-consent policy (T0.4).

Consulted by RecordingService before creating a recording, and by the
call origination path to decide whether a pre-answer announcement must
play. Keeps the compliance decision in one place so the audio pipeline
stays simple.

The DB row lives in `tenant_recording_policy`. If no row exists for a
tenant, we fall back to a SAFE default (two-party consent, announcement
required) — never silently record.

2026-07-28 — SPOKEN-DISCLOSURE LEDGER
-------------------------------------
`announcement_required` used to be advisory and nothing ever acted on
it: the only consumer was `RecordingService.save_and_link()`, which runs
AFTER the call has ended and only gates the upload. The disclosure was
therefore never spoken, while two-party-consent tenants (the SAFE
DEFAULT for every tenant with no policy row) were still recorded — i.e.
unlawful recording in the UK/EU and in two-party-consent US states.

The fix has two halves:

* the greeting path speaks the disclosure BEFORE any other audio, and
* it reports what actually happened into the ledger below, which
  `save_and_link` consults so audio captured without the promised
  notice is never retained.

The ledger is deliberately process-local and in-memory: it records a
fact about a live call that only this process observed, and it must
fail CLOSED (an unknown call reads as "no disclosure"), so losing it on
restart can only ever suppress a recording — never permit one.
"""
from __future__ import annotations

import logging
import re
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)


# Callers can treat this as an enum in practice.
CONSENT_ONE_PARTY = "one_party"
CONSENT_TWO_PARTY = "two_party"
CONSENT_DISABLED = "disabled"


@dataclass
class RecordingDecision:
    """Outcome of a recording-policy check.

    `should_record` is the gate — if False the RecordingService skips
    the whole upload pipeline. `announcement_required` is advisory:
    the origination path plays the announcement before pipeline start
    when set.
    """
    should_record: bool
    announcement_required: bool
    announcement_text: Optional[str]
    opt_out_dtmf_digit: Optional[str]
    retention_days: int
    reason: str  # machine-readable; logged for audit


# Safe default when the tenant has no row yet — opt-in, two-party,
# announce. Better to over-protect than under-protect on a first boot.
_SAFE_DEFAULT = RecordingDecision(
    should_record=True,
    announcement_required=True,
    announcement_text=(
        "This call may be recorded for quality and training purposes. "
        "Press 9 at any time to opt out of recording."
    ),
    opt_out_dtmf_digit="9",
    retention_days=90,
    reason="tenant_default_two_party",
)


class RecordingPolicyService:
    """Thin wrapper around `tenant_recording_policy`."""

    def __init__(self, db_pool: Any):
        self._db_pool = db_pool

    async def decide(
        self,
        *,
        tenant_id: str,
        destination_country_code: Optional[str] = None,
    ) -> RecordingDecision:
        """Return the recording decision for a specific outbound call.

        `destination_country_code` is an ISO-3166 alpha-2 (e.g. "GB", "DE")
        or the more specific "US-CA" / "US-MA" form used by the default
        two-party list for US states. Pass None if you don't know — we
        default to two-party in that case.
        """
        row = await self._load_row(tenant_id)
        if row is None:
            logger.info(
                "recording_policy_default_applied tenant=%s — no explicit "
                "policy row, using safe two-party default",
                tenant_id,
            )
            return _SAFE_DEFAULT

        mode = row["default_consent_mode"]
        retention_days = int(row["retention_days"])
        announcement_text = row["announcement_text"]
        dtmf = row["opt_out_dtmf_digit"]
        two_party_codes = set(row["two_party_country_codes"] or [])

        if mode == CONSENT_DISABLED:
            return RecordingDecision(
                should_record=False,
                announcement_required=False,
                announcement_text=None,
                opt_out_dtmf_digit=None,
                retention_days=retention_days,
                reason="tenant_policy_disabled",
            )

        if mode == CONSENT_ONE_PARTY:
            # Record without announcement — legal only in one-party-consent
            # jurisdictions. The tenant is asserting responsibility.
            return RecordingDecision(
                should_record=True,
                announcement_required=False,
                announcement_text=None,
                opt_out_dtmf_digit=None,
                retention_days=retention_days,
                reason="tenant_policy_one_party",
            )

        # two_party — announcement required iff destination is in the
        # list, OR the list is empty (safe default = announce everywhere).
        needs_announcement = (
            not two_party_codes
            or _country_matches_any(destination_country_code, two_party_codes)
        )
        return RecordingDecision(
            should_record=True,
            announcement_required=needs_announcement,
            announcement_text=announcement_text if needs_announcement else None,
            opt_out_dtmf_digit=dtmf if needs_announcement else None,
            retention_days=retention_days,
            reason=(
                "tenant_policy_two_party_announce"
                if needs_announcement
                else "tenant_policy_two_party_no_announce"
            ),
        )

    # ──────────────────────────────────────────────────────────────────

    async def _load_row(self, tenant_id: str) -> Optional[dict]:
        try:
            async with self._db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM tenant_recording_policy WHERE tenant_id = $1",
                    tenant_id,
                )
        except Exception as exc:
            logger.error(
                "recording_policy_lookup_failed tenant=%s err=%s — applying safe default",
                tenant_id, exc,
            )
            return None
        return dict(row) if row else None


# ──────────────────────────────────────────────────────────────────────
# Spoken-disclosure ledger
# ──────────────────────────────────────────────────────────────────────

DISCLOSURE_SPOKEN = "spoken"          # notice was played to the callee
DISCLOSURE_NOT_REQUIRED = "not_required"  # policy says none is needed
DISCLOSURE_FAILED = "failed"          # required but NOT delivered

# call_id -> state. Bounded FIFO: a call whose entry has been evicted
# reads back as "unknown", which `consent_satisfied` treats exactly like
# "not delivered" — eviction can only suppress a recording, never permit
# one. 10k entries is far more than the number of calls that can be in
# flight, so eviction only ever touches long-finished calls.
_DISCLOSURE_LEDGER: "OrderedDict[str, str]" = OrderedDict()
_LEDGER_MAX_ENTRIES = 10_000


def record_disclosure_state(state: str, *call_ids: Optional[str]) -> None:
    """Record what happened to the spoken disclosure for a call.

    Accepts several ids for the SAME call because the identifier differs
    between the two ends of this contract: the greeting path knows the
    voice-session UUID (and, once ``bind_telephony_call`` has run, the
    ``calls.id``), while ``save_and_link`` is handed whichever of those
    the teardown path resolved. Registering every known alias makes the
    later lookup id-agnostic.
    """
    for call_id in call_ids:
        if not call_id:
            continue
        key = str(call_id)
        _DISCLOSURE_LEDGER[key] = state
        _DISCLOSURE_LEDGER.move_to_end(key)
    while len(_DISCLOSURE_LEDGER) > _LEDGER_MAX_ENTRIES:
        _DISCLOSURE_LEDGER.popitem(last=False)


def get_disclosure_state(*call_ids: Optional[str]) -> Optional[str]:
    """Return the recorded state under any of `call_ids`, else None."""
    for call_id in call_ids:
        if not call_id:
            continue
        state = _DISCLOSURE_LEDGER.get(str(call_id))
        if state is not None:
            return state
    return None


def clear_disclosure_state(*call_ids: Optional[str]) -> None:
    """Drop ledger entries for a finished call (best-effort cleanup)."""
    for call_id in call_ids:
        if call_id:
            _DISCLOSURE_LEDGER.pop(str(call_id), None)


def consent_satisfied(decision: Any, *call_ids: Optional[str]) -> bool:
    """True when this call's audio may lawfully be retained.

    Two ways to satisfy it:
      1. the policy does not require an announcement at all, or
      2. the announcement was actually SPOKEN on this call.

    Anything else — required-but-failed, or an unknown call — is False.
    We deliberately fail CLOSED: audio captured while the callee was
    never told is unlawful to keep, whereas dropping a recording only
    costs the tenant a QA artefact.

    `announcement_required` is read via getattr so a duck-typed decision
    (existing tests hand `save_and_link` a stub carrying only
    `should_record`) is treated as "no announcement required" and keeps
    its current behaviour instead of raising.
    """
    if not getattr(decision, "announcement_required", False):
        return True
    return get_disclosure_state(*call_ids) == DISCLOSURE_SPOKEN


def disclosure_known_failed(*call_ids: Optional[str]) -> bool:
    """True only when we POSITIVELY know the disclosure was required and
    did not happen. Unknown calls return False — this is the predicate
    used by paths that never consulted the policy themselves, so it must
    not turn "no information" into a silent behaviour change."""
    return get_disclosure_state(*call_ids) == DISCLOSURE_FAILED


# ──────────────────────────────────────────────────────────────────────
# Disclosure text
# ──────────────────────────────────────────────────────────────────────

# Flip to True ONLY when a DTMF digit pressed by the callee actually
# stops the recording end-to-end (PBX DTMF event -> live call handler ->
# recording suppressed). Until then the opt-out sentence is stripped
# from the spoken notice: promising "press 9 to opt out" and then
# ignoring the keypress is a false statement of the callee's rights, and
# is worse than offering no opt-out at all.
DTMF_OPT_OUT_SUPPORTED = False

# Used when stripping the unhonoured opt-out leaves nothing to say.
MINIMUM_DISCLOSURE_TEXT = (
    "This call is being recorded for quality and training purposes."
)

# Matches an opt-out OFFER specifically — "opt out"/"opt-out", or an
# instruction to press/dial/key a DTMF character. Kept tight so an
# ordinary notice sentence is never mistaken for an opt-out offer.
_OPT_OUT_SENTENCE = re.compile(
    r"(?i)(?:\bopt[\s-]?out\b|\b(?:press|dial|key)\b\s+(?:the\s+)?[0-9*#])"
)


def spoken_disclosure_text(decision: Any) -> Optional[str]:
    """The text to actually SPEAK for `decision`.

    Returns None when no announcement is required. Otherwise returns the
    tenant's configured text with any opt-out offer removed while
    `DTMF_OPT_OUT_SUPPORTED` is False, falling back to
    `MINIMUM_DISCLOSURE_TEXT` if that removes everything.
    """
    if not getattr(decision, "announcement_required", False):
        return None

    text = (getattr(decision, "announcement_text", None) or "").strip()
    if not text:
        return MINIMUM_DISCLOSURE_TEXT
    if DTMF_OPT_OUT_SUPPORTED:
        return text

    kept = [
        part for part in re.split(r"(?<=[.!?;])\s+", text)
        if part.strip() and not _OPT_OUT_SENTENCE.search(part)
    ]
    cleaned = " ".join(kept).strip()
    # A clause that used to run on into the opt-out offer can be left
    # dangling on a ";" or ","; end it as a sentence so TTS reads it
    # cleanly.
    if cleaned.endswith((";", ",")):
        cleaned = cleaned[:-1].rstrip() + "."
    if not cleaned:
        logger.warning(
            "recording_disclosure_text_empty_after_opt_out_strip — "
            "configured text was only an opt-out offer; speaking the "
            "minimum notice instead"
        )
        return MINIMUM_DISCLOSURE_TEXT
    if cleaned != text:
        logger.info(
            "recording_disclosure_opt_out_stripped — DTMF opt-out is not "
            "wired, so the spoken notice omits the opt-out offer"
        )
    return cleaned


def _country_matches_any(cc: Optional[str], codes: set[str]) -> bool:
    """Match either a raw alpha-2 ("DE") or a subdivision ("US-CA")
    against the configured list. An empty callee country defaults to
    match — we don't know enough to skip the announcement safely."""
    if not cc:
        return True
    cc = cc.upper()
    if cc in codes:
        return True
    # If we got a subdivision like "US-CA", also match the plain "US"
    # entry. If we got a country like "US" and the list has a more
    # specific "US-CA", we conservatively return False (won't announce);
    # the caller will typically pass the subdivision when it matters.
    if "-" in cc and cc.split("-", 1)[0] in codes:
        return True
    return False
