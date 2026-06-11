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

from dataclasses import dataclass


@dataclass(frozen=True)
class IssueAdvice:
    title: str
    suggestion: str
    severity: str   # "error" (blocks calling, needs action) | "warning" (will retry on its own) | "info"
    stage: str      # quota | schedule | campaign | caller_id | voice | safety | concurrency | carrier | other


# Order matters — first match wins. Keys are matched as substrings against
# the lower-cased reason code.
_RULES: list[tuple[str, IssueAdvice]] = [
    ("out_of_minutes", IssueAdvice(
        "Out of plan minutes",
        "This account has used all its monthly call minutes. Add minutes or upgrade the plan, then start the campaign again.",
        "error", "quota",
    )),
    ("caller_id_not_verified", IssueAdvice(
        "Caller ID not verified",
        "No verified caller ID is set for this account. Register and verify a phone number in Settings → Phone Numbers, then set it as the campaign's caller ID.",
        "error", "caller_id",
    )),
    ("campaign_stopped", IssueAdvice(
        "Campaign is stopped",
        "The campaign isn't running, so queued calls are skipped. Press Start to begin dialing.",
        "warning", "campaign",
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

_FALLBACK = IssueAdvice(
    "Call could not be placed",
    "The call was held back for the reason shown. If it doesn't clear on its own, check the campaign's settings (caller ID, voice provider, calling hours) or contact support.",
    "warning", "other",
)


def advise(reason_code: str | None, *, category: str | None = None) -> IssueAdvice:
    """Map a raw reason (failure_reason / last_error / status) to advice.

    ``category`` is the dialer's coarse failure_category, used only as a
    weak tiebreaker for otherwise-unmatched reasons.
    """
    low = (reason_code or "").strip().lower()
    if low:
        for needle, advice in _RULES:
            if needle in low:
                return advice
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
