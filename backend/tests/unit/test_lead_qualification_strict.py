"""Owner decision 2026-10-21 ("only clear interest"): a contact is a lead only on
real interest (pricing/details/info/meeting/estimate) or an agreed callback inside
08:00-21:00 (the callback is the interest). Bad-time replies and odd-hour callbacks are
not leads (live evidence 2026-10-20: "Callback requested for Sunday 2 AM")."""
from app.domain.services.call_summary.store import _outcome_is_lead, _summary_supports_lead


def _lead(summary: dict) -> bool:
    return _outcome_is_lead(str(summary.get("outcome") or "")) and _summary_supports_lead(summary)[0]


_ROMAN = {
    "headline": "Callback requested for Sunday 2 AM",
    "outcome": "callback — caller asked to be called back Sunday at 2 AM",
    "qualification_status": "unknown",
    "timeline": "Sunday, 2 o'clock AM",
    "what_happened": "Caller said it was a bad time, then asked for a callback at 2 o'clock AM on Sunday.",
    "key_points": ["Said it is a bad time"],
    "commitments": ["Callback Sunday 2 AM"],
    "action_items": [{"item": "Call back Sunday 2 AM", "owner": "agent"}],
    "next_step": "Ask for phone number confirmation",
    "follow_up_tips": [],
    "business_details": [],
}


def test_roman_bad_time_then_2am_callback_is_not_a_lead():
    assert not _lead(_ROMAN)


def test_callback_in_hours_with_interest_is_a_lead():
    s = {**_ROMAN, "outcome": "callback — Tuesday 3 PM", "timeline": "Tuesday 3 PM", "commitments": ["Callback Tuesday 3 PM"],
         "action_items": [{"item": "Call back Tuesday 3 PM", "owner": "agent"}],
         "headline": "Callback Tuesday 3 PM", "what_happened": "Asked about pricing, wants a call Tuesday at 3 PM.",
         "key_points": ["Asked about pricing"], "next_step": "Call Tuesday 3 PM"}
    assert _lead(s)


def test_callback_agreed_inside_calling_hours_is_a_lead():
    """Owner's rule: agreeing to a callback inside calling hours IS the interest."""
    s = {**_ROMAN, "outcome": "callback — Tuesday 3 PM", "timeline": "Tuesday 3 PM", "commitments": ["Callback Tuesday 3 PM"],
         "action_items": [], "headline": "Callback Tuesday 3 PM",
         "what_happened": "Caller was busy and asked for a call Tuesday at 3 PM.",
         "key_points": ["Busy"], "next_step": "Call Tuesday 3 PM"}
    assert _lead(s)


def test_bare_callback_no_time_no_interest_is_not_a_lead():
    s = {"outcome": "callback", "qualification_status": "unknown", "commitments": ["call back"],
         "action_items": [], "next_step": "Call back", "key_points": [], "what_happened": "Asked to be called back."}
    assert not _lead(s)


def test_callback_with_interest_but_no_determinable_time_is_not_a_lead():
    s = {"outcome": "callback", "qualification_status": "qualified", "commitments": ["call back Sunday"],
         "action_items": [], "next_step": "Call back Sunday", "key_points": ["Asked about pricing"]}
    assert not _lead(s)


def test_qualified_with_pricing_interest_is_a_lead():
    s = {"outcome": "qualified — wants pricing", "qualification_status": "qualified",
         "commitments": [], "action_items": [], "next_step": "Send pricing details",
         "key_points": ["Asked for pricing and details"]}
    assert _lead(s)


def test_qualified_without_any_interest_signal_is_not_a_lead():
    s = {"outcome": "qualified", "qualification_status": "unknown", "commitments": ["x"],
         "next_step": "Follow up", "key_points": ["Said it is a bad time"], "what_happened": "Bad time."}
    assert not _lead(s)


def test_disqualified_never_a_lead():
    s = {"outcome": "disqualified — asked for pricing", "qualification_status": "qualified",
         "commitments": ["x"], "next_step": "Send pricing", "key_points": ["pricing"]}
    assert not _lead(s)


# Review 2026-10-21: callback-time parsing reads only the agreed-time fields.

def _callback(time_text, **extra):
    return {**_ROMAN, "outcome": f"callback — {time_text}", "headline": f"Callback {time_text}",
            "commitments": [f"Callback {time_text}"], "action_items": [],
            "what_happened": "Caller asked for a callback.", "key_points": [], **extra}


def test_noon_and_afternoon_callbacks_are_inside_hours():
    assert _lead(_callback("tomorrow at noon"))
    assert _lead(_callback("tomorrow afternoon"))


def test_a_bare_7_30_reads_as_evening_not_morning():
    assert _lead(_callback("at 7:30"))


def test_any_stated_time_inside_hours_counts():
    assert _lead(_callback("not at 7 am, call at 2 pm"))


def test_durations_in_the_narrative_are_not_callback_times():
    s = _callback("Tuesday 3 PM", what_happened="At 1:30 into the call the caller asked for Tuesday 3 PM.",
                  timeline="00:05 greeting, 01:30 callback agreed")
    assert _lead(s)


def test_negated_interest_is_not_interest():
    # no qualification_status, so the interest wording itself is what is judged
    s = {"outcome": "qualified", "commitments": ["follow up"],
         "action_items": [], "next_step": "Follow up", "key_points": ["Wasn't interested in pricing"],
         "what_happened": "No longer interested."}
    assert _summary_supports_lead(s) == (False, "no_clear_interest_signal")


def test_estimation_interest_counts():
    s = {"outcome": "qualified — wants an estimate",
         "commitments": [], "action_items": [], "next_step": "Send an estimate for the takeoff",
         "key_points": ["Wants an estimate"]}
    assert _lead(s)
