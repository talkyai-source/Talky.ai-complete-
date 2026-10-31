"""Post-call capture saves CLEAN, concise values, with the caller's words as evidence.

Production, 13 Oct 2026 (browser test calls on campaign "Dojo-PC"): the
post-call path wrote ``call_lead_details`` rows such as

    preferred_channel = "Yeah, I can WhatsApp. I'm also looking for business funding..."
    callback_request  = "Yeah, he can do."

i.e. the WHOLE caller sentence as the value, because ``verified_details``
replaced the model's value with the caller turn. Nothing was saved for timing
("as soon as possible") because ``timeline`` was not a business field, and the
campaign's own fields (job_title, best_time_to_call, calling_notes ...) were
ignored because the path only knew seven fixed keys.

The invariants below replace the old "value == the whole caller turn" contract:

* ``value`` is the model's short faithful rewording; the full caller turn stays
  in ``evidence.source_quote``; status stays ``needs_review`` (still an
  unverified model interpretation).
* A negation in the caller's turn that the rewording dropped means the meaning
  could be inverted, so the caller's own turn is kept instead.
* A later correction still invalidates an earlier candidate.
* The campaign's NON-contact fields are extracted too; email/phone/identity
  fields are never written post-call (they need caller confirmation through the
  live record_contact tool); a manual, confirmed or live row is never replaced;
  a select field accepts only its listed options.
"""
from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domain.services.call_summary import store
from app.domain.services.call_summary.business_details import (
    BUSINESS_FIELDS,
    extractable_campaign_fields,
    persist_business_details,
    verified_details,
)
from app.domain.services.call_summary.summarizer import (
    _OBJECT_LIST_ITEMS,
    _SYSTEM_PROMPT,
    summarize_transcript,
)
from app.domain.services.lead_capture_service import LeadCaptureService

TENANT = "00000000-0000-0000-0000-000000000001"
CALL = "00000000-0000-0000-0000-000000000002"
CAMPAIGN = "00000000-0000-0000-0000-000000000003"


def turns(*texts):
    return [{"role": "user", "content": text} for text in texts]


def detail(key, quote, value):
    return {"field_key": key, "source_quote": quote, "value": value}


# The fields the Dojo-PC style campaigns define (field_type per the table's CHECK).
CAMPAIGN_FIELDS = [
    {"field_key": "email", "label": "Email", "field_type": "email", "options": None},
    {"field_key": "company_name", "label": "Company", "field_type": "text", "options": None},
    {"field_key": "job_title", "label": "Job title", "field_type": "text", "options": None},
    {"field_key": "best_time_to_call", "label": "Best time to call", "field_type": "text", "options": None},
    {"field_key": "calling_notes", "label": "Calling notes", "field_type": "notes", "options": None},
]


# --------------------------------------------------------------------------
# 1. A concise value is stored; the caller's whole turn is the evidence
# --------------------------------------------------------------------------


def test_concise_model_value_is_stored_and_the_whole_caller_turn_is_the_evidence():
    turn = "Yeah, I can WhatsApp. I'm also looking for business funding to buy more stock."
    [found] = verified_details([detail("preferred_channel", "I can WhatsApp", "WhatsApp")], turns(turn))
    assert found["value"] == "WhatsApp"
    assert found["field_type"] == "notes"
    assert found["evidence"]["source_quote"] == turn
    assert found["evidence"]["turn_index"] == 0
    # Still an unverified model interpretation, never certified.
    assert found["evidence"]["status"] == "needs_review"
    assert found["evidence"]["validation"] == "caller_quote_only"
    assert found["evidence"]["extraction"] == "post_call"
    assert found["evidence"]["value_basis"] == "model_summary"


@pytest.mark.parametrize("key,turn,quote,value", [
    ("preferred_channel", "Yeah, I can WhatsApp. I'm also looking for business funding.",
     "I can WhatsApp", "WhatsApp"),
    ("identified_need", "It's for improvement of the business, buying stock mostly.",
     "improvement of the business, buying stock", "Funding for stock and business improvement"),
    ("timeline", "As soon as possible please.", "As soon as possible", "As soon as possible"),
    ("timeline", "Sometime is fine, no rush.", "Sometime is fine", "No fixed deadline"),
])
def test_the_13_oct_production_answers_become_short_clean_values(key, turn, quote, value):
    [found] = verified_details([detail(key, quote, value)], turns(turn))
    assert found["value"] == value
    assert found["value"] != turn
    assert found["evidence"]["source_quote"] == turn


def test_value_is_bounded_free_of_control_characters_and_single_spaced():
    turn = "I would like WhatsApp please."
    [found] = verified_details([detail("preferred_channel", "WhatsApp", "  \"Whats\x00App\tmessage\n\"  ")], turns(turn))
    assert not any(ord(ch) < 32 or ord(ch) == 127 for ch in found["value"])
    assert "  " not in found["value"] and found["value"] == found["value"].strip()
    assert found["value"].startswith("Whats")


def test_a_value_that_is_not_concise_falls_back_to_the_callers_own_turn():
    turn = "I would like WhatsApp please."
    [found] = verified_details([detail("preferred_channel", "WhatsApp", "x" * 500)], turns(turn))
    assert found["value"] == turn
    assert found["evidence"]["value_basis"] == "caller_turn"


def test_a_long_caller_turn_fallback_is_bounded_so_the_write_cannot_be_refused():
    turn = "WhatsApp is fine. " + "I will keep talking about the business. " * 400
    [found] = verified_details([detail("preferred_channel", "WhatsApp is fine", "x" * 500)], turns(turn))
    assert 0 < len(found["value"]) <= 4000
    assert found["evidence"]["source_quote"] == turn.strip()


def test_the_model_cannot_add_figures_or_dates_the_caller_did_not_say():
    spoken = turns("We need about five thousand pounds.")
    [invented] = verified_details([detail("identified_need", "five thousand pounds", "Funding of 5000 pounds")], spoken)
    assert invented["value"] == "We need about five thousand pounds."
    assert invented["evidence"]["value_basis"] == "caller_turn"
    said = turns("We need about 5,000 pounds.")
    [grounded] = verified_details([detail("identified_need", "5,000 pounds", "Funding of 5000 pounds")], said)
    assert grounded["value"] == "Funding of 5000 pounds"


# --------------------------------------------------------------------------
# 2. Polarity: never invert what the caller said
# --------------------------------------------------------------------------


@pytest.mark.parametrize("negation", ["not", "no", "never", "don't", "doesn't", "isn't", "won't", "can't", "don’t"])
def test_a_negation_the_rewording_dropped_keeps_the_callers_own_turn(negation):
    turn = f"Honestly I {negation} want Stripe, I use SumUp."
    [found] = verified_details([detail("current_provider", "I use SumUp", "Stripe")], turns(turn))
    assert found["value"] == turn
    assert found["evidence"]["value_basis"] == "caller_turn"
    assert found["evidence"]["source_quote"] == turn
    assert found["evidence"]["status"] == "needs_review"


def test_a_rewording_that_keeps_the_negation_is_accepted():
    turn = "No, we don't use Dojo, we use Worldpay."
    [kept] = verified_details([detail("current_provider", "we use Worldpay", "Worldpay, not Dojo")], turns(turn))
    assert kept["value"] == "Worldpay, not Dojo"
    assert kept["evidence"]["value_basis"] == "model_summary"
    [dropped] = verified_details([detail("current_provider", "we use Worldpay", "Uses Dojo")], turns(turn))
    assert dropped["value"] == turn


def test_a_word_that_merely_contains_no_is_not_a_negation():
    turn = "I know the number and the notes already, WhatsApp works."
    [found] = verified_details([detail("preferred_channel", "WhatsApp works", "WhatsApp")], turns(turn))
    assert found["value"] == "WhatsApp"


# --------------------------------------------------------------------------
# 3. A later correction still invalidates earlier candidates
# --------------------------------------------------------------------------


def test_a_later_correction_still_invalidates_an_earlier_candidate():
    spoken = turns(
        "I need funding for stock.",
        "Actually, never mind the funding.",
        "Ring me as soon as possible.",
    )
    result = verified_details([
        detail("identified_need", "funding for stock", "Funding for stock"),
        detail("timeline", "as soon as possible", "As soon as possible"),
    ], spoken)
    # The need was withdrawn; the timeline was said AFTER the correction.
    assert [d["field_key"] for d in result] == ["timeline"]
    assert result[0]["value"] == "As soon as possible"


# --------------------------------------------------------------------------
# 4. timeline is a business field
# --------------------------------------------------------------------------


def test_timeline_is_a_business_field_and_the_prompt_and_schema_ask_for_it():
    assert "timeline" in BUSINESS_FIELDS
    listed = re.search(r'"field_key": "<([a-z_|]+)>"', _SYSTEM_PROMPT).group(1).split("|")
    enum = _OBJECT_LIST_ITEMS["business_details"]["properties"]["field_key"]["enum"]
    # Prompt, JSON schema and the verifier must agree on the vocabulary.
    assert set(listed) == set(enum) == set(BUSINESS_FIELDS)
    assert "timeline" in listed


def test_timeline_is_saved_as_an_unresolved_note():
    [found] = verified_details(
        [detail("timeline", "as soon as possible", "As soon as possible")],
        turns("I'd like to move on this as soon as possible."),
    )
    assert found["field_key"] == "timeline"
    assert found["value"] == "As soon as possible"
    assert found["field_type"] == "notes"
    assert found["evidence"]["time_resolution"] == "needs_review"


def test_the_prompt_asks_for_a_short_value_not_the_exact_caller_wording():
    assert "<exact caller wording>" not in _SYSTEM_PROMPT
    assert "source_quote" in _SYSTEM_PROMPT


# --------------------------------------------------------------------------
# 5. The campaign's own non-contact fields
# --------------------------------------------------------------------------


def test_campaign_text_fields_are_extracted_with_their_own_type():
    spoken = turns(
        "I'm the operations manager there.",
        "Mornings are best for me.",
        "He also wants the brochure by post.",
    )
    result = verified_details([
        detail("job_title", "operations manager", "Operations manager"),
        detail("best_time_to_call", "Mornings are best", "Mornings"),
        detail("calling_notes", "brochure by post", "Wants the brochure by post"),
    ], spoken, campaign_fields=CAMPAIGN_FIELDS)
    by_key = {d["field_key"]: d for d in result}
    assert {k: (d["value"], d["field_type"]) for k, d in by_key.items()} == {
        "job_title": ("Operations manager", "text"),
        "best_time_to_call": ("Mornings", "text"),
        "calling_notes": ("Wants the brochure by post", "notes"),
    }
    assert by_key["job_title"]["evidence"]["source_quote"] == "I'm the operations manager there."
    assert by_key["job_title"]["evidence"]["status"] == "needs_review"
    assert by_key["job_title"]["evidence"]["extraction"] == "post_call"


def test_a_campaign_field_the_model_was_not_given_is_ignored():
    result = verified_details([detail("job_title", "operations manager", "Operations manager")],
                              turns("I'm the operations manager."))
    assert result == []


def test_the_campaign_may_redefine_a_fixed_key_with_its_own_type():
    fields = [{"field_key": "timeline", "label": "Timeline", "field_type": "text"}]
    [found] = verified_details([detail("timeline", "next month", "Next month")],
                               turns("Probably next month."), campaign_fields=fields)
    assert found["field_type"] == "text"


# --------------------------------------------------------------------------
# 6. Contact and identity fields are never written post-call
# --------------------------------------------------------------------------

CONTACT_AND_IDENTITY = [
    {"field_key": "email", "label": "Email", "field_type": "email"},
    {"field_key": "work_email", "label": "Work email", "field_type": "email"},
    {"field_key": "phone_number", "label": "Phone", "field_type": "phone"},
    {"field_key": "mobile_number", "label": "Mobile", "field_type": "phone"},
    {"field_key": "business_number", "label": "Business number", "field_type": "phone"},
    {"field_key": "phone_number_text", "label": "Phone (text)", "field_type": "phone"},
    {"field_key": "phone", "label": "Phone key typed text", "field_type": "text"},
    {"field_key": "email_2", "label": "Second email typed text", "field_type": "text"},
    {"field_key": "first_name", "label": "First name", "field_type": "text"},
    {"field_key": "last_name", "label": "Last name", "field_type": "text"},
    {"field_key": "full_name", "label": "Name", "field_type": "text"},
    {"field_key": "company_name", "label": "Company", "field_type": "text"},
    {"field_key": "contact_followup", "label": "Reserved", "field_type": "notes"},
]


def test_extractable_campaign_fields_drops_contact_identity_and_reserved_fields():
    rows = CONTACT_AND_IDENTITY + CAMPAIGN_FIELDS
    assert [f["field_key"] for f in extractable_campaign_fields(rows)] == [
        "job_title", "best_time_to_call", "calling_notes"]


def test_contact_and_identity_fields_are_never_written_post_call():
    spoken = turns(
        "My email is sam at example dot com and my number is 07700 900123.",
        "Sam Taylor from Acme, I'm the operations manager.",
    )
    wanted = [
        ("email", "my email is sam at example dot com", "sam@example.com"),
        ("work_email", "my email is sam at example dot com", "sam@example.com"),
        ("phone_number", "my number is 07700 900123", "07700 900123"),
        ("mobile_number", "my number is 07700 900123", "07700 900123"),
        ("business_number", "my number is 07700 900123", "07700 900123"),
        ("phone", "my number is 07700 900123", "07700 900123"),
        ("email_2", "my email is sam at example dot com", "sam@example.com"),
        ("first_name", "Sam Taylor", "Sam"),
        ("last_name", "Sam Taylor", "Taylor"),
        ("full_name", "Sam Taylor", "Sam Taylor"),
        ("company_name", "from Acme", "Acme"),
        ("contact_followup", "from Acme", "Acme"),
        ("job_title", "operations manager", "Operations manager"),
    ]
    result = verified_details([detail(*w) for w in wanted], spoken,
                              campaign_fields=CONTACT_AND_IDENTITY + CAMPAIGN_FIELDS)
    assert [d["field_key"] for d in result] == ["job_title"]


# --------------------------------------------------------------------------
# 8. Select fields accept only their listed options
# --------------------------------------------------------------------------

OPTIONS = ["WhatsApp", "Phone call", "Email"]


@pytest.mark.parametrize("options", [OPTIONS, json.dumps(OPTIONS)], ids=["list", "jsonb-as-text"])
def test_single_select_accepts_only_a_listed_option(options):
    fields = [{"field_key": "channel_choice", "label": "Channel", "field_type": "single_select", "options": options}]
    spoken = turns("WhatsApp is best for me.")
    [ok] = verified_details([detail("channel_choice", "WhatsApp is best", "whatsapp")], spoken, campaign_fields=fields)
    assert ok["value"] == "WhatsApp" and ok["field_type"] == "single_select"
    assert ok["evidence"]["source_quote"] == "WhatsApp is best for me."
    # An answer that is not one of the options is not accepted (and is not
    # replaced by the caller's sentence, which is not an option either).
    assert verified_details([detail("channel_choice", "WhatsApp is best", "Carrier pigeon")], spoken,
                            campaign_fields=fields) == []
    assert verified_details([detail("channel_choice", "WhatsApp is best", "WhatsApp message")], spoken,
                            campaign_fields=fields) == []


def test_multi_select_keeps_only_listed_options():
    fields = [{"field_key": "channels", "label": "Channels", "field_type": "multi_select", "options": OPTIONS}]
    spoken = turns("Email or WhatsApp, either is fine.")
    [found] = verified_details([detail("channels", "Email or WhatsApp", "email, whatsapp, smoke signals")],
                               spoken, campaign_fields=fields)
    assert found["field_type"] == "multi_select"
    assert found["value"] == ["Email", "WhatsApp"]
    assert verified_details([detail("channels", "Email or WhatsApp", "smoke signals")], spoken,
                            campaign_fields=fields) == []


def test_a_select_field_without_options_is_not_extractable():
    fields = [{"field_key": "channel_choice", "label": "Channel", "field_type": "single_select", "options": None},
              {"field_key": "other_choice", "label": "Other", "field_type": "multi_select", "options": "not json"}]
    assert extractable_campaign_fields(fields) == []


def test_a_select_answer_whose_meaning_cannot_be_checked_against_a_negation_is_dropped():
    fields = [{"field_key": "interest", "label": "Interest", "field_type": "single_select",
               "options": ["Interested", "Not interested"]}]
    spoken = turns("I'm not interested at all.")
    assert verified_details([detail("interest", "not interested", "Interested")], spoken, campaign_fields=fields) == []
    [ok] = verified_details([detail("interest", "not interested", "Not interested")], spoken, campaign_fields=fields)
    assert ok["value"] == "Not interested"


def test_number_fields_keep_only_a_number_the_caller_actually_said():
    fields = [{"field_key": "staff_count", "label": "Staff", "field_type": "number"}]
    [ok] = verified_details([detail("staff_count", "12 people", "12")], turns("We have 12 people."),
                            campaign_fields=fields)
    assert ok["value"] == "12" and ok["field_type"] == "number"
    assert verified_details([detail("staff_count", "twelve people", "12")], turns("We have twelve people."),
                            campaign_fields=fields) == []
    assert verified_details([detail("staff_count", "lots of people", "many")], turns("We have lots of people."),
                            campaign_fields=fields) == []


def test_datetime_fields_are_not_given_a_date_the_caller_never_said():
    fields = [{"field_key": "demo_date", "label": "Demo date", "field_type": "datetime"}]
    [found] = verified_details([detail("demo_date", "next Thursday", "Next Thursday")], turns("Let's do next Thursday."),
                               campaign_fields=fields)
    assert found["value"] == "Next Thursday"
    assert found["field_type"] == "notes"
    assert found["evidence"]["time_resolution"] == "needs_review"
    [iso] = verified_details([detail("demo_date", "2026-10-20", "2026-10-20")], turns("Make it 2026-10-20."),
                             campaign_fields=fields)
    assert iso["field_type"] == "datetime"


# --------------------------------------------------------------------------
# Persistence: real types, unconfirmed, and nothing protected is overwritten
# --------------------------------------------------------------------------


class Db:
    """A connection fake: records SQL, answers the protected-row read."""

    def __init__(self, protected=()):
        self.conn = AsyncMock()
        self.conn.fetch = AsyncMock(return_value=[{"field_key": key} for key in protected])

    @asynccontextmanager
    async def acquire(self, *args):
        yield self.conn


@pytest.fixture
def db(monkeypatch):
    holder = Db()
    monkeypatch.setattr("app.core.db_utils.acquire_with_tenant", holder.acquire)
    return holder


@pytest.fixture
def capture(monkeypatch):
    save = AsyncMock(return_value=True)
    monkeypatch.setattr(LeadCaptureService, "capture", save)
    return save


@pytest.fixture
def campaign_rows(monkeypatch):
    rows = AsyncMock(return_value=[dict(f) for f in CAMPAIGN_FIELDS])
    monkeypatch.setattr(LeadCaptureService, "fields_for_campaign", rows)
    return rows


def call_row(*texts):
    spoken = turns(*texts)
    return {"campaign_id": CAMPAIGN, "lead_id": "lead", "transcript": "\n".join("User: " + t for t in texts),
            "transcript_json": spoken, "action_results": {}}


async def test_campaign_fields_are_saved_post_call_unconfirmed_with_their_real_type(db, capture, campaign_rows):
    row = call_row("I'm the operations manager.", "Mornings are best.", "Ring me as soon as possible.")
    summary = {"business_details": [
        detail("job_title", "operations manager", "Operations manager"),
        detail("best_time_to_call", "Mornings are best", "Mornings"),
        detail("timeline", "as soon as possible", "As soon as possible"),
        detail("email", "operations manager", "boss@example.com"),
    ]}
    assert await persist_business_details(object(), TENANT, CALL, summary, row) == 3
    campaign_rows.assert_awaited_once_with(TENANT, CAMPAIGN)
    saved = {c.kwargs["field_key"]: c.kwargs for c in capture.await_args_list}
    assert set(saved) == {"job_title", "best_time_to_call", "timeline"}
    assert (saved["job_title"]["value"], saved["job_title"]["field_type"]) == ("Operations manager", "text")
    assert (saved["best_time_to_call"]["value"], saved["best_time_to_call"]["field_type"]) == ("Mornings", "text")
    assert (saved["timeline"]["value"], saved["timeline"]["field_type"]) == ("As soon as possible", "notes")
    for kwargs in saved.values():
        assert kwargs["source"] == "caller_stated" and kwargs["confirmed"] is False
        assert kwargs["campaign_id"] == CAMPAIGN and kwargs["lead_id"] == "lead"
        assert kwargs["evidence"]["extraction"] == "post_call"
        assert kwargs["evidence"]["status"] == "needs_review"
        assert kwargs["evidence"]["transcript_revision"] == kwargs["expected_summary_hash"]
    # The revision DELETE is still the last statement run on the connection.
    assert "DELETE FROM call_lead_details" in db.conn.execute.await_args.args[0]


async def test_a_call_without_a_campaign_never_reads_campaign_fields(db, capture, campaign_rows):
    row = {**call_row("Ring me as soon as possible."), "campaign_id": None}
    summary = {"business_details": [detail("timeline", "as soon as possible", "As soon as possible")]}
    assert await persist_business_details(object(), TENANT, CALL, summary, row) == 1
    campaign_rows.assert_not_awaited()


async def test_manual_confirmed_and_live_rows_are_never_overwritten(db, capture, campaign_rows):
    # What the call already holds that the post-call path does not own: an
    # operator's manual edit, a confirmed value, a live-captured row.
    db.conn.fetch.return_value = [{"field_key": "job_title"}, {"field_key": "identified_need"}]
    row = call_row("I'm the operations manager.", "I need a card terminal.", "Mornings are best.")
    summary = {"business_details": [
        detail("job_title", "operations manager", "Operations manager"),
        detail("identified_need", "card terminal", "Card terminal"),
        detail("best_time_to_call", "Mornings are best", "Mornings"),
    ]}
    assert await persist_business_details(object(), TENANT, CALL, summary, row) == 1
    assert [c.kwargs["field_key"] for c in capture.await_args_list] == ["best_time_to_call"]
    # The read that decides it is tenant-scoped and treats only this path's own
    # unconfirmed post-call rows as replaceable.
    sql, call_id, tenant_id, read_fields = db.conn.fetch.await_args.args
    assert (call_id, tenant_id) == (CALL, TENANT)
    assert sorted(read_fields) == ["best_time_to_call", "identified_need", "job_title"]
    assert "tenant_id" in sql and "manual_edit" in sql and "post_call" in sql and "confirmed" in sql


async def test_one_refused_value_does_not_lose_the_other_details(db, monkeypatch, campaign_rows):
    from app.domain.services.lead_capture_service import InvalidCaptureError

    async def refuse_the_first(**kwargs):
        if kwargs["field_key"] == "job_title":
            raise InvalidCaptureError("synthetic refusal")
        return True

    monkeypatch.setattr(LeadCaptureService, "capture", AsyncMock(side_effect=refuse_the_first))
    row = call_row("I'm the operations manager.", "Mornings are best.")
    summary = {"business_details": [
        detail("job_title", "operations manager", "Operations manager"),
        detail("best_time_to_call", "Mornings are best", "Mornings"),
    ]}
    assert await persist_business_details(object(), TENANT, CALL, summary, row) == 1


# --------------------------------------------------------------------------
# The summariser asks for the campaign's fields; the store hands them over
# --------------------------------------------------------------------------


def fake_groq(captured):
    async def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({"headline": "ok"})))])

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(side_effect=create))))


async def test_the_summariser_prompt_and_strict_schema_carry_the_campaigns_fields():
    captured = {}
    fields = CONTACT_AND_IDENTITY + CAMPAIGN_FIELDS + [
        {"field_key": "channel_choice", "label": "Channel", "field_type": "single_select", "options": OPTIONS}]
    with patch("app.domain.services.call_summary.summarizer.AsyncGroq", return_value=fake_groq(captured)), \
            patch("app.domain.services.call_summary.summarizer.summariser_model", return_value="openai/gpt-oss-20b"):
        await summarize_transcript("User: I'm the operations manager.", campaign_fields=fields)
    system = captured["messages"][0]["content"]
    assert system.startswith(_SYSTEM_PROMPT)
    assert '"field_key": "job_title"' in system and "Best time to call" in system
    assert '"options": ["WhatsApp", "Phone call", "Email"]' in system
    # The model is never even asked for contact or identity fields.
    for key in ("email", "phone_number", "company_name", "first_name", "full_name", "contact_followup"):
        assert f'"field_key": "{key}"' not in system
    enum = (captured["response_format"]["json_schema"]["schema"]["properties"]["business_details"]
            ["items"]["properties"]["field_key"]["enum"])
    assert {"timeline", "job_title", "best_time_to_call", "calling_notes", "channel_choice"} <= set(enum)
    assert not {"email", "phone_number", "company_name", "first_name", "full_name"} & set(enum)
    # The shared module-level schema is not mutated for the next call.
    base = _OBJECT_LIST_ITEMS["business_details"]["properties"]["field_key"]["enum"]
    assert "job_title" not in base


async def test_the_summariser_prompt_is_unchanged_for_a_campaign_without_fields():
    captured = {}
    with patch("app.domain.services.call_summary.summarizer.AsyncGroq", return_value=fake_groq(captured)):
        await summarize_transcript("User: hello there")
        assert captured["messages"][0]["content"] == _SYSTEM_PROMPT
        await summarize_transcript("User: hello there", campaign_fields=CONTACT_AND_IDENTITY)
        assert captured["messages"][0]["content"] == _SYSTEM_PROMPT


def store_conn(row):
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=row)
    conn.execute = AsyncMock(return_value=None)

    @asynccontextmanager
    async def acquire(pool, tenant_id):
        yield conn

    return conn, acquire


async def test_the_store_hands_the_summariser_the_campaigns_extractable_fields(monkeypatch):
    row = {"transcript": "User: I'm the operations manager.", "summary_json": None, "campaign_id": CAMPAIGN,
           "transcript_json": turns("I'm the operations manager.")}
    conn, acquire = store_conn(row)
    monkeypatch.setattr(store, "acquire_with_tenant", acquire)
    monkeypatch.setattr(store, "_confirmed_contacts_for_call", AsyncMock(return_value={}))
    rows = AsyncMock(return_value=[dict(f) for f in CAMPAIGN_FIELDS])
    monkeypatch.setattr(LeadCaptureService, "fields_for_campaign", rows)
    summarize = AsyncMock(return_value={"headline": "ok", "business_details": []})
    monkeypatch.setattr(store, "summarize_transcript", summarize)
    for name in ("save_summary_details", "refresh_latest_analysis", "mark_lead_from_summary"):
        monkeypatch.setattr(store, name, AsyncMock())
    await store.generate_and_store(object(), TENANT, CALL)
    rows.assert_awaited_once_with(TENANT, CAMPAIGN)
    handed = summarize.await_args.kwargs["campaign_fields"]
    assert [f["field_key"] for f in handed] == ["job_title", "best_time_to_call", "calling_notes"]
    assert [f["field_type"] for f in handed] == ["text", "text", "notes"]
