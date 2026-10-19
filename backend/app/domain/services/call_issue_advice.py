"""Turn a raw dialer/telephony failure code into an operator-facing card:
a short title, an actionable suggestion, a severity, and the pipeline
stage it failed at.

This is the single source of truth behind ``GET /calls/issues`` so the
frontend stays dumb (just renders what it's given). Matching is by
substring/prefix because several codes carry a suffix, e.g.
``outside_time_window_09:00_19:00`` or ``max_concurrent_calls_reached_10/10``.

Why this exists: every pre-dial gate (out of minutes, outside hours,
campaign stopped, caller-ID unverified, TTS warmup timeout, rate limits)
fails in the dialer BEFORE a ``calls`` row exists, so the live-calls panel
never showed them — the operator was blind to why nothing dialed. This
maps those raw reasons to "here's what happened and how to fix it".
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class IssueAdvice:
    title: str
    suggestion: str
    severity: str   # "error" (blocks calling, needs action) | "warning" (will retry on its own) | "info"
    stage: str      # quota | schedule | campaign | caller_id | voice | safety | concurrency | carrier | other


_SCHEDULE_INVALID = IssueAdvice(
    "Calling hours are invalid",
    "No calls are going out because the calling hours can't be used: the start time must be before the end time on the same day (24-hour HH:MM, not past midnight). Fix the calling hours in the campaign's calling rules and calls resume within the hour.",
    "error", "schedule",
)

_LINE_CALLER_ID_UNVERIFIED = IssueAdvice(
    "Phone line caller ID not verified",
    "The caller ID on this phone line is not verified yet. Ask support to verify it; calls resume automatically once it is.",
    "error", "caller_id",
)

_NO_LINE_READY = IssueAdvice(
    "No phone line ready",
    "No phone line is ready to dial out. Connect or switch on a line in Settings → Telephony. The call retries automatically once a line is registered.",
    "error", "carrier",
)

_LINE_NOT_REGISTERED = IssueAdvice(
    "Campaign phone line not registered",
    "The phone line for this campaign is not registered with the carrier right now. Check its status in Settings → Telephony, or assign a different line to the campaign.",
    "error", "carrier",
)

_SERVER_ERROR = IssueAdvice(
    "Calling service error",
    "The calling service hit an internal error while placing this call. It is retried automatically; if it keeps happening, contact support.",
    "warning", "other",
)

# Order matters — first match wins. Keys are matched as substrings against
# the lower-cased reason code.
_RULES: list[tuple[str, IssueAdvice]] = [
    ("out_of_minutes", IssueAdvice(
        "Out of plan minutes",
        "This account has used all its monthly call minutes. Add minutes or upgrade the plan, then start the campaign again.",
        "error", "quota",
    )),
    # Must stay ABOVE ``caller_id_not_verified``: it is a substring match.
    ("trunk_caller_id_not_verified", _LINE_CALLER_ID_UNVERIFIED),
    ("caller_id_not_verified", IssueAdvice(
        "Caller ID not verified",
        "The caller ID these calls go out from is not verified for this account yet. Ask support to verify the number; calls resume automatically once it is verified.",
        "error", "caller_id",
    )),
    ("campaign_stopped", IssueAdvice(
        "Campaign is stopped",
        "The campaign isn't running, so queued calls are skipped. Press Start to begin dialing.",
        "warning", "campaign",
    )),
    ("campaign_not_runnable", IssueAdvice(
        "Campaign is not running",
        "The campaign isn't running, so queued calls are skipped. Press Start to begin dialing.",
        "warning", "campaign",
    )),
    ("campaign_paused", IssueAdvice(
        "Campaign is paused",
        "The campaign is paused, so its queued calls are on hold. Press Start to resume dialing.",
        "warning", "campaign",
    )),
    ("job_not_active", IssueAdvice(
        "Queued call no longer active",
        "This queued call was cancelled or already handled, so it was skipped. No action needed.",
        "info", "campaign",
    )),
    ("lead_not_dialable", IssueAdvice(
        "Contact can't be dialed",
        "This contact can't be dialed right now: it may have been deleted, opted out, or have no valid phone number. Fix the contact or remove it from the campaign.",
        "warning", "campaign",
    )),
    # Phone line / route refusals (trunk_resolver + outbound readiness).
    ("tenant_pbx_required", IssueAdvice(
        "Phone line not ready",
        "The campaign's phone line or caller ID is not ready, so the call was refused before dialing. Check the line in Settings → Telephony; the call retries automatically once it is registered.",
        "error", "carrier",
    )),
    ("no_ready_trunk", _NO_LINE_READY),
    ("no_own_trunk", _NO_LINE_READY),
    ("selected_trunk_not_ready", _LINE_NOT_REGISTERED),
    ("campaign_assigned_trunk_not_ready", _LINE_NOT_REGISTERED),
    ("line_not_ready", IssueAdvice(
        "Phone line not ready",
        "The call was refused because no usable phone line was available: no line is switched on, the line is not registered with the carrier, or its caller ID is not verified. Check Settings → Telephony; if the caller ID is the problem, ask support to verify it. The call retries automatically.",
        "error", "carrier",
    )),
    # Must precede ``telephony_unavailable``: it is a substring of this code.
    ("cloud_telephony_unavailable", IssueAdvice(
        "Cloud calling unavailable",
        "Cloud calling is switched off in production. Select a ready phone line in Settings → Telephony to place calls.",
        "error", "carrier",
    )),
    ("telephony_unavailable", IssueAdvice(
        "Phone system not connected",
        "The calling service is not connected to the phone system right now. This normally clears on its own and the call resumes automatically; if it lasts more than a few minutes, contact support.",
        "warning", "carrier",
    )),
    ("telephony_selection_unavailable", IssueAdvice(
        "Telephony settings unavailable",
        "The account's saved phone-system selection could not be checked just now, so the call was held back. It retries automatically; if it persists, contact support.",
        "warning", "carrier",
    )),
    ("origination_result_unknown", IssueAdvice(
        "Call result unknown",
        "The call was handed to the phone system but its result did not come back, so it is held to avoid dialing the same person twice. It is settled automatically once the status is confirmed.",
        "warning", "carrier",
    )),
    # Account / platform state and held-back checks.
    ("metering_unavailable", IssueAdvice(
        "Minute allowance can't be checked",
        "The plan-minutes check was unavailable, so the call was held back rather than risk going over the allowance. It retries automatically; if it persists, contact support.",
        "warning", "quota",
    )),
    ("rules_unavailable", IssueAdvice(
        "Calling rules unavailable",
        "The campaign's calling rules (hours and limits) could not be loaded, so the call was held back for safety. It retries automatically; if it persists, contact support.",
        "warning", "schedule",
    )),
    ("guard_check_unavailable", IssueAdvice(
        "Safety check unavailable",
        "A safety check (such as the Do-Not-Call or rate-limit lookup) could not be completed, so the call was held back instead of skipping it. It retries automatically; if it persists, contact support.",
        "warning", "safety",
    )),
    ("platform_outbound_calls_paused", IssueAdvice(
        "Calling paused platform-wide",
        "Outbound calling is temporarily paused for the whole platform by the operator. Calls resume automatically when it is switched back on; contact support for an estimate.",
        "warning", "safety",
    )),
    ("tenant_suspended", IssueAdvice(
        "Account suspended",
        "This account is suspended, so no calls are placed. Contact support to reactivate it.",
        "error", "other",
    )),
    ("subscription_past_due", IssueAdvice(
        "Subscription payment overdue",
        "The subscription payment is overdue, so calling is paused. Update the payment method in Billing; calls resume once the payment goes through.",
        "error", "quota",
    )),
    ("outside_business_hours", IssueAdvice(
        "Outside business hours",
        "The current time is outside the configured business hours, so the call was held back. It will go out when business hours start, or change the business hours in your settings.",
        "warning", "schedule",
    )),
    # TESTING MODE must be recognised BEFORE the schedule rules below — its
    # raw code mentions the schedule, but it is the opposite of a block: the
    # calling window was deliberately bypassed and the operator has to see it.
    ("testing_mode_schedule_bypassed", IssueAdvice(
        "TESTING MODE: schedule bypassed",
        "Calls are going out even though the current time is outside the configured calling hours, because the testing override is switched on. This is for testing only — turn it off before real calling, since calling-hour rules exist for legal/compliance reasons.",
        "warning", "schedule",
    )),
    # The window itself is invalid (unreadable, or ends before it starts): it
    # never opens, so it needs a person, not a wait.
    ("invalid_time_window", _SCHEDULE_INVALID),
    ("schedule_invalid", _SCHEDULE_INVALID),
    # Day-of-week gate. The raw dialer code is `calling_not_allowed_on_Tue`
    # (from CallingRules.is_within_time_window) — it contains neither
    # "time_window" nor "day", so before this rule existed it fell through to
    # the generic fallback card and the user was told nothing useful.
    ("calling_not_allowed_on", IssueAdvice(
        "Outside calling days",
        "Today isn't one of this campaign's allowed calling days. It will dial automatically on the next allowed day, or add today to the calling days in the campaign's calling rules.",
        "warning", "schedule",
    )),
    ("schedule_day_not_allowed", IssueAdvice(
        "Outside calling days",
        "Today isn't one of this campaign's allowed calling days. It will dial automatically on the next allowed day, or add today to the calling days in the campaign's calling rules.",
        "warning", "schedule",
    )),
    ("schedule_outside_window", IssueAdvice(
        "Outside calling hours",
        "The current time is outside this campaign's allowed calling window. It will dial automatically when the window opens, or widen the window in the campaign's calling rules.",
        "warning", "schedule",
    )),
    ("outside_time_window", IssueAdvice(
        "Outside calling hours",
        "The current time is outside this campaign's allowed calling window. It will dial automatically when the window opens, or widen the window in the campaign's calling rules.",
        "warning", "schedule",
    )),
    ("outside_calling_window", IssueAdvice(
        "Outside calling hours",
        "The current time is outside this campaign's allowed calling window. It will dial automatically when the window opens, or widen the window in the campaign's calling rules.",
        "warning", "schedule",
    )),
    ("max_concurrent_calls_reached", IssueAdvice(
        "Concurrency limit reached",
        "This account is already running the maximum number of simultaneous calls. The call will retry automatically as slots free up, or raise the limit in calling rules.",
        "warning", "concurrency",
    )),
    ("lead_cooldown", IssueAdvice(
        "Lead recently called",
        "This lead was called recently and is in its cooldown period. It will be retried automatically after the minimum gap between calls.",
        "warning", "schedule",
    )),
    ("min_hours_between_calls", IssueAdvice(
        "Lead recently called",
        "This lead was called within the minimum gap between calls. It will be retried automatically once the cooldown passes.",
        "warning", "schedule",
    )),
    # Voice pipeline — provider-SPECIFIC rules first (more actionable), then
    # the generic pre-warm fallbacks. A warmup failure that names a provider
    # (e.g. "pre_originate_warmup...: ElevenLabs 401") should get the
    # provider advice, not the generic one.
    ("quota_exceeded", IssueAdvice(
        "Voice provider out of credits",
        "The text-to-speech provider is out of credits/quota. Top it up or switch the campaign's voice provider.",
        "error", "voice",
    )),
    ("elevenlabs", IssueAdvice(
        "Voice provider error (ElevenLabs)",
        "ElevenLabs rejected or failed the request (often an invalid API key or no credits). Verify the ElevenLabs key, or switch the campaign's voice provider to Cartesia.",
        "error", "voice",
    )),
    ("cartesia", IssueAdvice(
        "Voice provider error (Cartesia)",
        "Cartesia failed to synthesize speech. Check the Cartesia API key/voice, or switch the campaign's voice provider.",
        "error", "voice",
    )),
    ("assemblyai", IssueAdvice(
        "Speech-to-text error (AssemblyAI)",
        "AssemblyAI could not start or continue speech recognition. Check the AssemblyAI API key, streaming access and concurrency limit, then retry or select another speech-recognition engine in AI Options.",
        "error", "voice",
    )),
    ("deepgram", IssueAdvice(
        "Speech-to-text error",
        "The speech-to-text provider (Deepgram) failed, so the agent couldn't hear the caller. Check the Deepgram key/health.",
        "error", "voice",
    )),
    # Generic pre-warm fallbacks (no provider named).
    ("voice_pipeline_unavailable", IssueAdvice(
        "Voice provider not ready",
        "The voice pipeline (text-to-speech / speech-to-text) wasn't ready in time, so the call was held back to avoid dead air on pickup. Check the campaign's voice-provider API key/health, or switch the provider (e.g. to Cartesia). It will keep retrying automatically.",
        "error", "voice",
    )),
    ("pre_originate_warmup", IssueAdvice(
        "Voice provider not ready",
        "The voice pipeline (text-to-speech / speech-to-text) didn't become ready in time, so the call was held back to avoid dead air on pickup. Check the campaign's voice-provider API key/health, or switch the provider (e.g. to Cartesia).",
        "error", "voice",
    )),
    ("service_unavailable", IssueAdvice(
        "Voice provider not ready",
        "The voice pipeline wasn't ready (a TTS/STT provider didn't respond). Check the provider's API key/credits, or switch the campaign's voice provider.",
        "error", "voice",
    )),
    # Safety / CallGuard.
    ("call_guard_blocked", IssueAdvice(
        "Blocked by safety rules",
        "The call was blocked by a safety check — the number may be on a Do-Not-Call list, or blocked by geographic/abuse rules. Remove it from the campaign or review your safety settings.",
        "error", "safety",
    )),
    ("call_guard_throttled", IssueAdvice(
        "Rate limited",
        "Calls are going out faster than the per-account limit allows. The dialer will retry shortly — no action needed.",
        "warning", "safety",
    )),
    # Pacing — normal operation, never an operator problem. These are also
    # excluded from GET /calls/issues at the SQL level; the entries exist so
    # any other surface that maps a reason renders them as benign info.
    ("call_gap", IssueAdvice(
        "Pacing between calls",
        "The campaign spaces its calls (inter-call gap). This number dials automatically when its turn comes — no action needed.",
        "info", "pacing",
    )),
    ("batch_capacity", IssueAdvice(
        "Waiting for a batch slot",
        "The campaign dials in batches; this number starts as soon as an in-flight call finishes — no action needed.",
        "info", "pacing",
    )),
    ("tenant_gap", IssueAdvice(
        "Pacing across campaigns",
        "Another campaign of this account just placed a call; campaigns take turns so only one call originates at a time — no action needed.",
        "info", "pacing",
    )),
    ("call_guard_queued", IssueAdvice(
        "Waiting for a free slot",
        "The call is queued behind the account's rate limiter and will dial automatically when a slot frees up.",
        "info", "safety",
    )),
    ("dnc", IssueAdvice(
        "On Do-Not-Call list",
        "This number is on a Do-Not-Call list and won't be dialed. Remove it from the campaign.",
        "error", "safety",
    )),
    # Carrier / destination.
    ("408", IssueAdvice(
        "Carrier didn't answer (timeout)",
        "The carrier timed out reaching the destination — the number may be invalid or unreachable. Verify the phone number.",
        "warning", "carrier",
    )),
    ("unreachable", IssueAdvice(
        "Number unreachable",
        "The carrier couldn't reach this number. Verify it's a valid, dialable number.",
        "warning", "carrier",
    )),
    ("no_answer", IssueAdvice(
        "No answer",
        "The call rang but wasn't answered. It will retry per the campaign's retry policy.",
        "info", "carrier",
    )),
    ("busy", IssueAdvice(
        "Line busy",
        "The destination was busy. It will retry per the campaign's retry policy.",
        "info", "carrier",
    )),
    ("adapter not connected", IssueAdvice(
        "Telephony not connected",
        "The telephony bridge isn't connected to the carrier. This is a server-side issue — contact support if it persists.",
        "error", "carrier",
    )),
    ("telephony_not_active_on_node", IssueAdvice(
        "Telephony not active on this node",
        "The request reached a server process that isn't the telephony owner. It retries automatically against the right one; if it persists, a second API worker may be misconfigured.",
        "warning", "carrier",
    )),
]

# One-word dialer outcomes. Matched only as the WHOLE leading token of the
# reason (``failed``, ``rejected (403)``, ``http_503``) — as substrings they
# would hijack longer reasons that have a provider rule or a failure category
# (``..._handshake_failed: ElevenLabs 401``).
_GENERIC_RULES: dict[str, IssueAdvice] = {
    "rejected": IssueAdvice(
        "Call rejected",
        "The phone system refused the call straight away, before it rang. Check that the number is valid and dialable, and that the campaign's phone line is registered in Settings → Telephony.",
        "warning", "carrier",
    ),
    "failed": IssueAdvice(
        "Call failed",
        "The call failed before it connected. Check the number and the campaign's phone line and voice settings; if it keeps happening, contact support.",
        "warning", "other",
    ),
    "internal_error": _SERVER_ERROR,
}
_HTTP_5XX = re.compile(r"http_5\d\d")


def _generic_advice(low: str) -> IssueAdvice | None:
    token = re.split(r"[\s:(\[]", low, maxsplit=1)[0]
    if token in _GENERIC_RULES:
        return _GENERIC_RULES[token]
    if _HTTP_5XX.fullmatch(token):
        return _SERVER_ERROR
    return None


_FALLBACK = IssueAdvice(
    "Call could not be placed",
    "The call was held back for the reason shown. If it doesn't clear on its own, check the campaign's settings (caller ID, voice provider, calling hours) or contact support.",
    "warning", "other",
)


# Short dialer codes that are also substrings of unrelated codes (for example
# ``line_not_ready`` inside ``voice_pipeline_not_ready``): match them only as a
# whole token, bounded by start/end or a non-word separator such as ``:``.
_TOKEN_NEEDLES = frozenset({
    "line_not_ready", "telephony_unavailable", "campaign_paused",
    "job_not_active", "lead_not_dialable",
})


def _token_match(needle: str, low: str) -> bool:
    import re

    return re.search(rf"(?:^|[^a-z0-9_]){re.escape(needle)}(?:$|[^a-z0-9_])", low) is not None


def advise(reason_code: str | None, *, category: str | None = None) -> IssueAdvice:
    """Map a raw reason (failure_reason / last_error / status) to advice.

    ``category`` is the dialer's coarse failure_category, used only as a
    weak tiebreaker for otherwise-unmatched reasons.
    """
    low = (reason_code or "").strip().lower()
    if low:
        for needle, advice in _RULES:
            if needle in _TOKEN_NEEDLES:
                if _token_match(needle, low):
                    return advice
            elif needle in low:
                return advice
        generic = _generic_advice(low)
        if generic is not None:
            return generic
    # Fall back on the coarse category when the specific reason is unknown.
    cat = (category or "").strip().lower()
    if cat in {"tts", "stt", "llm", "prewarm"}:
        return _RULES[next(i for i, (n, _) in enumerate(_RULES) if n == "pre_originate_warmup")][1]
    if cat == "telephony":
        return IssueAdvice(
            "Telephony/carrier error",
            "The carrier or telephony layer failed to place the call. Verify the number; if it persists, contact support.",
            "warning", "carrier",
        )
    return _FALLBACK
