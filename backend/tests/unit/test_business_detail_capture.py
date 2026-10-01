import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from app.domain.services.call_summary.business_details import verified_details, persist_business_details


def item(key, quote, value=None):
    return {"field_key": key, "source_quote": quote, "value": value or quote}


def test_only_actual_caller_evidence_can_be_saved():
    turns = [{"role": "assistant", "content": "You need a new terminal."},
             {"role": "user", "content": "I use Stripe and prefer WhatsApp."}]
    result = verified_details([
        item("identified_need", "You need a new terminal."),
        item("current_provider", "I use Stripe", "Stripe"),
        item("preferred_channel", "prefer WhatsApp", "WhatsApp"),
        item("budget", "I have a big budget"),
    ], turns)
    assert [d["field_key"] for d in result] == ["current_provider", "preferred_channel"]
    assert result[0]["value"] == "I use Stripe and prefer WhatsApp."
    assert result[0]["evidence"]["status"] == "needs_review"
    assert result[0]["evidence"]["turn_index"] == 1


def test_correction_wins_independent_of_model_array_order():
    turns = [{"role": "user", "content": "Call me Thursday."},
             {"role": "user", "content": "Cancel that callback."}]
    result = verified_details([item("callback_request", "Cancel that callback."),
                               item("callback_request", "Call me Thursday.")], turns)
    assert result[0]["value"] == "Cancel that callback."
    assert result[0]["evidence"]["status"] == "needs_review"


def test_referral_never_becomes_callers_canonical_contact():
    quote = "My colleague Sam can discuss it on 020 7946 0958."
    result = verified_details([item("referral", quote), item("phone", quote)],
                              [{"role": "user", "content": quote}])
    assert len(result) == 1
    assert result[0]["field_key"] == "referral"
    assert result[0]["evidence"]["subject"] == "referral"


def test_post_call_cannot_invent_normalized_dates_or_confirmation():
    quote = "Call me next Thursday."
    result = verified_details([item("callback_request", quote, "2026-10-08T09:00:00Z")],
                              [{"role": "user", "content": quote}])
    assert result[0]["value"] == quote
    assert result[0]["evidence"]["time_resolution"] == "needs_review"
    assert result[0]["evidence"]["status"] == "needs_review"


@pytest.mark.parametrize("quote", ["I do not use Stripe.", "My brother uses Stripe; I do not."])
def test_word_presence_never_certifies_polarity_or_subject(quote):
    result = verified_details([item("current_provider", "Stripe", "Stripe")],
                              [{"role": "user", "content": quote}])
    assert result[0]["value"] == quote
    assert result[0]["evidence"]["status"] == "needs_review"
    assert result[0]["evidence"]["subject"] == "unverified"


def test_omitted_later_correction_invalidates_an_older_model_candidate():
    turns = [{"role": "user", "content": "I use Stripe."},
             {"role": "user", "content": "Actually I use SumUp, not Stripe."}]
    assert verified_details([item("current_provider", "I use Stripe.", "Stripe")], turns) == []
    corrected = verified_details([item("current_provider", turns[1]["content"], "SumUp")], turns)
    assert corrected[0]["value"] == turns[1]["content"]


def test_interim_and_excluded_transcripts_cannot_become_business_notes():
    assert verified_details([item("current_provider", "I use Stripe.")], [
        {"role": "user", "content": "I use Stripe.", "is_final": False},
        {"role": "user", "content": "I use Stripe.", "include_in_plaintext": False},
    ]) == []


def test_negated_withdrawal_is_not_labelled_as_a_withdrawal():
    quote = "Please don't cancel my callback."
    result = verified_details([item("callback_request", quote)], [{"role": "user", "content": quote}])
    assert result[0]["value"] == quote
    assert result[0]["evidence"]["status"] == "needs_review"


@pytest.mark.asyncio
async def test_persistence_is_call_scoped_unconfirmed_and_revision_bound(monkeypatch):
    from app.domain.services.lead_capture_service import LeadCaptureService
    conn = AsyncMock()
    @asynccontextmanager
    async def acquire(*args):
        yield conn
    monkeypatch.setattr("app.core.db_utils.acquire_with_tenant", acquire)
    save = AsyncMock(return_value=True)
    monkeypatch.setattr(LeadCaptureService, "capture", save)
    quote = "I need better support."
    row = {"campaign_id": "campaign", "lead_id": "lead", "transcript": "User: " + quote,
           "transcript_json": [{"role": "user", "content": quote}]}
    summary = {"business_details": [item("identified_need", quote, "better support")]}
    assert await persist_business_details(object(), "tenant", "call", summary, row) == 1
    args = save.await_args.kwargs
    assert args["source"] == "caller_stated" and args["confirmed"] is False
    assert args["expected_transcript"] == row["transcript"]
    assert args["evidence"]["source_quote"] == quote
    assert args["lead_id"] == "lead" and args["call_id"] == "call"
    assert args["expected_summary_hash"] == args["evidence"]["transcript_revision"]
    delete = conn.execute.await_args.args
    assert "source <> 'manual_edit'" in delete[0]
    assert "summary_transcript_hash=$3" in delete[0]
    assert delete[1:3] == ("call", "tenant")
