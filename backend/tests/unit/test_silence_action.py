"""Unit tests for the natural caller-first silence-monitor decision.

Covers the product flow: caller-first agent waits; ~10s → a soft "Hello?";
caller speaks → normal; 60s of caller silence → auto-close. Pure decision, so
the flow is verifiable without a live audio pipeline.

2026-08-11: the OPENING re-greet cadence was retuned to feel like a human who
just dialled — "Hello?" again after a natural ~2-3s pause, not once every
10-15s (see audio_ingest.py's docstring for the full rationale). That added
one new parameter to ``silence_action``: ``opening_gap_s``, an OPENING-only
repeat gap independent of the MID ``nudge_gap_s``. It defaults to ``None``
(falls back to ``nudge_gap_s``), which is why every test above this comment
needed zero changes — they never pass it, so they still exercise the exact
pre-2026-08-11 shared-gap behaviour. The cadence-specific cases below are new.
"""
from app.domain.services.voice_pipeline.audio_ingest import silence_action

_KW = dict(hangup_s=60.0, opening_s=10.0, mid_s=10.0, nudge_gap_s=12.0)

# Mirrors the real production defaults wired in audio_ingest.py after the
# 2026-08-11 retune (VOICE_OPENING_HELLO_S / VOICE_OPENING_NUDGE_GAP_S both
# 2.5s) — a human's actual re-"Hello?" cadence.
_PROD_OPENING_KW = dict(
    hangup_s=60.0, opening_s=2.5, mid_s=10.0, nudge_gap_s=15.0, opening_gap_s=2.5,
)


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


# ── 2026-08-11 retune: OPENING has its own, much shorter, repeat gap ────
def test_opening_gap_is_independent_of_the_mid_gap():
    # nudge_gap_s (mid) is 12.0 here — far too long for a re-"Hello?" cadence.
    # opening_gap_s=2.5 is what actually gates the opening ladder's repeats;
    # 3.0s since the last nudge clears it even though it wouldn't clear the
    # mid gap.
    assert _act(
        is_caller_first=True, user_turns=0,
        activity_silence_s=10.0, since_last_nudge_s=3.0, opening_gap_s=2.5,
    ) == "nudge"


def test_opening_gap_still_waits_before_its_own_threshold():
    assert _act(
        is_caller_first=True, user_turns=0,
        activity_silence_s=10.0, since_last_nudge_s=1.0, opening_gap_s=2.5,
    ) == "wait"


def test_opening_gap_defaults_to_the_mid_gap_when_not_supplied():
    # Backward compatibility: a caller that never learns about opening_gap_s
    # (e.g. an older test) gets exactly the old shared-gap behaviour.
    assert _act(
        is_caller_first=True, user_turns=0,
        activity_silence_s=10.0, since_last_nudge_s=3.0,
    ) == "wait"  # falls back to nudge_gap_s=12.0; 3.0 < 12.0


def test_opening_gap_never_applies_to_a_mid_conversation_tick():
    # opening_gap_s must only ever gate the OPENING ladder — a mid-call tick
    # (user_turns>0) keeps using nudge_gap_s even if opening_gap_s is passed.
    assert _act(
        is_caller_first=True, user_turns=2,
        activity_silence_s=10.0, since_last_nudge_s=3.0, opening_gap_s=2.5,
    ) == "wait"  # nudge_gap_s=12.0 still applies here, 3.0 < 12.0


# ── the new production cadence: first re-greet at ~2.5s, not 10s ────────
def test_first_regreet_fires_at_the_new_production_interval():
    assert silence_action(
        caller_silence_s=2.5, activity_silence_s=2.5, since_last_nudge_s=None,
        in_grace=False, is_caller_first=True, user_turns=0, **_PROD_OPENING_KW,
    ) == "nudge"


def test_first_regreet_waits_just_under_the_new_interval():
    assert silence_action(
        caller_silence_s=2.4, activity_silence_s=2.4, since_last_nudge_s=None,
        in_grace=False, is_caller_first=True, user_turns=0, **_PROD_OPENING_KW,
    ) == "wait"


# ── nudging never touches the caller-silence (hangup) clock ─────────────
def test_opening_regreets_never_reset_the_hangup_clock():
    # Even mid-ladder (a nudge just fired 0.5s ago), 60s of continuous CALLER
    # silence still hangs up — nudges only ever reset activity_silence_s /
    # since_last_nudge_s, never caller_silence_s, which is driven solely by
    # the caller actually speaking.
    assert silence_action(
        caller_silence_s=60.0, activity_silence_s=0.5, since_last_nudge_s=0.5,
        in_grace=False, is_caller_first=True, user_turns=0, **_PROD_OPENING_KW,
    ) == "hangup"


# ── grace still suppresses under the new, tighter opening thresholds ────
def test_grace_still_suppresses_opening_nudge_under_new_thresholds():
    assert silence_action(
        caller_silence_s=5.0, activity_silence_s=5.0, since_last_nudge_s=None,
        in_grace=True, is_caller_first=True, user_turns=0, **_PROD_OPENING_KW,
    ) == "wait"


# ── NOTE on boundedness: the per-call cap on the number of opening re-greets
# (VOICE_OPENING_MAX_NUDGES, default 3) is enforced by the _silence_monitor
# loop in audio_ingest.py, not by this pure function — silence_action decides
# one tick at a time and has no memory of how many nudges already fired. See
# test_opening_ladder_is_bounded_and_never_nags_past_its_cap in
# test_audio_ingest_caller_first_silence.py for the integration-level proof
# that the real loop actually stops nudging once the ladder is exhausted and
# falls through to the 60s hangup.


# ── 2026-08-12 regression: a bare hello with no follow-up ──────────────────
#
# Reported from a live test: "it stops after speaking one time hi there or
# hello, no follow up again." An AGENT-FIRST call fell into a hole once turn 1
# became a bare two-word greeting:
#
#   opening = is_caller_first and user_turns == 0   -> False (agent-first)
#             => the re-greet ladder never applied
#   should_suppress_mid_nudge(is_caller_first=False,
#                             caller_has_ever_spoken=False) -> True
#             => the mid nudge was skipped too
#
# Net: "Hi there." then total silence until the 60s hangup. This was safe only
# while agent-first opened with a full introduction ending in a question. A
# bare hello and the re-greet ladder are two halves of one design.

def _agent_first_silent_callee(**over):
    base = dict(
        caller_silence_s=3.0, activity_silence_s=3.0, since_last_nudge_s=None,
        in_grace=False, is_caller_first=False, user_turns=0,
        hangup_s=60.0, opening_s=2.5, mid_s=10.0,
        nudge_gap_s=15.0, opening_gap_s=2.5,
    )
    base.update(over)
    return base


def test_agent_first_bare_hello_still_gets_a_re_greet():
    """THE REGRESSION. Agent said a bare hello, callee silent 3s — the ladder
    must fire. Before the fix this returned 'wait' forever."""
    assert silence_action(
        **_agent_first_silent_callee(agent_awaiting_first_reply=True)
    ) == "nudge"


def test_agent_first_bare_hello_uses_the_OPENING_threshold_not_mid():
    """3s is past the 2.5s opening threshold but well short of the 10s mid
    threshold — proving it took the opening path."""
    assert silence_action(
        **_agent_first_silent_callee(
            caller_silence_s=3.0, activity_silence_s=3.0,
            agent_awaiting_first_reply=True,
        )
    ) == "nudge"
    # ...and at 1s, still inside the opening threshold, it waits.
    assert silence_action(
        **_agent_first_silent_callee(
            caller_silence_s=1.0, activity_silence_s=1.0,
            agent_awaiting_first_reply=True,
        )
    ) == "wait"


def test_once_introduced_the_agent_first_call_returns_to_the_MID_cadence():
    """Non-vacuity — the opening path must not swallow the whole call. After a
    real introduction, the slower mid threshold applies again."""
    assert silence_action(
        **_agent_first_silent_callee(
            caller_silence_s=3.0, activity_silence_s=3.0,
            agent_awaiting_first_reply=False,
        )
    ) == "wait"


def test_the_hangup_clock_is_untouched_by_the_fix():
    """60s of caller silence still closes the call, opening state or not."""
    assert silence_action(
        **_agent_first_silent_callee(
            caller_silence_s=61.0, activity_silence_s=61.0,
            agent_awaiting_first_reply=True,
        )
    ) == "hangup"


def test_grace_still_suppresses_the_new_opening_path():
    """The agent must never nudge over its own playback."""
    assert silence_action(
        **_agent_first_silent_callee(
            in_grace=True, agent_awaiting_first_reply=True,
        )
    ) == "wait"


def test_caller_first_behaviour_is_unchanged_by_the_new_parameter():
    """The default is False, so every pre-existing caller is byte-identical."""
    caller_first = dict(
        caller_silence_s=3.0, activity_silence_s=3.0, since_last_nudge_s=None,
        in_grace=False, is_caller_first=True, user_turns=0,
        hangup_s=60.0, opening_s=2.5, mid_s=10.0,
        nudge_gap_s=15.0, opening_gap_s=2.5,
    )
    assert silence_action(**caller_first) == "nudge"
    assert silence_action(**caller_first, agent_awaiting_first_reply=True) == "nudge"


# ── 2026-08-12: the mid nudge was interrupting people mid-thought ──────────
#
# Traced production call, twice in 90 seconds:
#     20:54:46.881  silence (mid), nudging: 'Still there?'
#     20:54:49.522  EndOfTurn: 'So what can I do about that ...'   (+2.6s)
#     20:55:00.009  silence (mid), nudging: 'Still there?'
#     20:55:01.639  EndOfTurn: 'So what what I do ...'             (+1.6s)
#
# The caller was composing a question both times. 10s of quiet mid-conversation
# is thinking, not a dead line.

def _mid(**over):
    base = dict(
        caller_silence_s=12.0, activity_silence_s=12.0, since_last_nudge_s=None,
        in_grace=False, is_caller_first=False, user_turns=3,
        hangup_s=60.0, opening_s=2.5, mid_s=16.0,
        nudge_gap_s=15.0, opening_gap_s=2.5,
    )
    base.update(over)
    return base


def test_twelve_seconds_of_thinking_is_no_longer_prodded():
    """THE REGRESSION. At the old 10s threshold this nudged; at 16s it waits."""
    assert silence_action(**_mid()) == "wait"
    assert silence_action(**_mid(mid_s=10.0)) == "nudge"   # the old behaviour


def test_a_genuinely_dead_line_is_still_checked():
    """Non-vacuity — raising the threshold must not disable the mid nudge."""
    assert silence_action(**_mid(caller_silence_s=17.0, activity_silence_s=17.0)) == "nudge"


def test_the_opening_threshold_is_untouched_by_the_mid_change():
    """A silent PICKUP really might be a dead line — that stays at 2.5s."""
    assert silence_action(
        **_mid(caller_silence_s=3.0, activity_silence_s=3.0,
               is_caller_first=True, user_turns=0)
    ) == "nudge"


def test_the_hangup_clock_is_unaffected():
    assert silence_action(**_mid(caller_silence_s=61.0, activity_silence_s=61.0)) == "hangup"
