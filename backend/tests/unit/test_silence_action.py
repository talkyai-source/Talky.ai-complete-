"""Unit tests for the natural caller-first silence-monitor decision.

Covers the product flow: caller-first agent waits; ~10s → a soft "Hello?";
caller speaks → normal; 60s of caller silence → auto-close. Pure decision, so
the flow is verifiable without a live audio pipeline.
"""
from app.domain.services.voice_pipeline.audio_ingest import silence_action

_KW = dict(hangup_s=60.0, opening_s=10.0, mid_s=10.0, nudge_gap_s=12.0)


def _act(**over):
    base = dict(
        caller_silence_s=0.0,
        activity_silence_s=0.0,
        since_last_nudge_s=None,
        in_grace=False,
        is_caller_first=True,
        user_turns=0,
        **_KW,
    )
    base.update(over)
    return silence_action(**base)


# ── grace suppresses everything ─────────────────────────────────────────
def test_grace_waits_even_at_hangup_threshold():
    assert _act(in_grace=True, caller_silence_s=90, activity_silence_s=90) == "wait"


# ── caller-first opening: ~10s of silence → a gentle nudge ───────────────
def test_opening_nudges_after_threshold():
    assert _act(is_caller_first=True, user_turns=0, activity_silence_s=10.0) == "nudge"


def test_opening_waits_before_threshold():
    assert _act(is_caller_first=True, user_turns=0, activity_silence_s=9.0) == "wait"


# ── once the caller has spoken it's mid-conversation, not opening ────────
def test_mid_conversation_uses_mid_threshold():
    # user_turns>0 → not opening; still nudges at the (equal) mid threshold.
    assert _act(is_caller_first=True, user_turns=2, activity_silence_s=10.0) == "nudge"


# ── the min gap between nudges is enforced ───────────────────────────────
def test_recent_nudge_waits():
    assert _act(activity_silence_s=30.0, since_last_nudge_s=5.0) == "wait"


def test_nudge_allowed_after_gap():
    assert _act(activity_silence_s=30.0, since_last_nudge_s=20.0) == "nudge"


# ── 60s of continuous CALLER silence → close the call ───────────────────
def test_hangup_at_caller_silence_threshold():
    # Caller silent 60s even though we nudged recently (nudges don't reset it).
    assert _act(caller_silence_s=60.0, activity_silence_s=2.0, since_last_nudge_s=2.0) == "hangup"


def test_no_hangup_just_under_threshold():
    # 59s < 60s caller silence → not a hangup.
    assert _act(caller_silence_s=59.0, activity_silence_s=2.0) == "wait"
    assert _act(caller_silence_s=59.0, activity_silence_s=59.0) == "nudge"


# ── agent-first (outbound) never uses the opening path ──────────────────
def test_agent_first_uses_mid_threshold_not_opening():
    # is_caller_first False → opening=False regardless of user_turns.
    assert _act(is_caller_first=False, user_turns=0, activity_silence_s=9.0) == "wait"
    assert _act(is_caller_first=False, user_turns=0, activity_silence_s=10.0) == "nudge"
