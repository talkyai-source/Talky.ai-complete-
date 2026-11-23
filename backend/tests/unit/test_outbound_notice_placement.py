"""Where the recording notice is said on outgoing calls (per-account choice).

The default `own_sentence_first` is today's behaviour for every input; the choice
`in_opening` has exactly the platform wording's proof rules. Reuses the fakes of
the existing opener, pre-made opener, Realtime and recording tests.
"""
from __future__ import annotations

import itertools
import logging
import types
from unittest.mock import AsyncMock, patch

import pytest

from app.domain.services import recording_policy_service as rps
from app.domain.services.notice_placement import (
    IN_OPENING,
    OWN_SENTENCE_FIRST,
    NoticePlacement,
    decide_notice_placement,
    normalize_placement,
    placement_for,
)
from app.domain.services.recording_policy_service import (
    RecordingDecision,
    is_platform_notice_text,
)
from app.domain.services.telephony.modes.agent_first import _speak_recording_disclosure
from app.domain.services.telephony.modes.caller_first import prepare_inbound_recording
from app.domain.services.voice_orchestrator import Direction
from app.domain.services.voice_pipeline.caller_first_disclosure import (
    speak_disclosure_before_first_reply,
)
from tests.unit import test_outbound_ai_disclosure_opener as opener_t
from tests.unit import test_premade_caller_first_opener as premade_t
from tests.unit import test_realtime_opening_notice as rt_t
from tests.unit.test_recording_disclosure import (
    CALL_UUID as DISC_CALL,
    _GatedInboundMediaGateway,
    _make_voice_session,
    _patched_policy,
)

OWN = "Acme records calls to train its team."
IDENTITY_PLAIN = "This is Sarah, an AI assistant from All State Estimation."
PLACEMENT_LOGGER = "app.domain.services.notice_placement"


def _decision(required=True, text=OWN, placement=IN_OPENING, mode_reason="test"):
    return RecordingDecision(
        should_record=True, announcement_required=required, announcement_text=text,
        opt_out_dtmf_digit=None, retention_days=90, reason=mode_reason,
        outbound_notice_placement=placement,
    )


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    rps._DISCLOSURE_LEDGER.clear()
    monkeypatch.delenv("OPENER_PREMADE_ENABLED", raising=False)
    monkeypatch.delenv(rt_t.on.ENV_SWITCH, raising=False)
    # These tests are about the account's choice, which the platform must have opened.
    # What happens while it is closed is in test_notice_placement_switch.py.
    monkeypatch.setenv("OUTBOUND_NOTICE_PLACEMENT_CHOICE_ENABLED", "true")
    yield
    rps._DISCLOSURE_LEDGER.clear()


def _lines(caplog):
    return [r.getMessage() for r in caplog.records
            if r.name == PLACEMENT_LOGGER and "recording_notice_placement" in r.getMessage()]


# 1
_TEXTS = [
    None, "", "   ", "This call may be recorded for quality and training purposes.",
    "  This call may be recorded   for quality and training purposes. ",
    rps.MINIMUM_DISCLOSURE_TEXT, f"  {rps.MINIMUM_DISCLOSURE_TEXT}\n", OWN,
    "This call may be recorded for quality and training purposes. Press 9 to opt out.",
]
_DECISIONS = [
    types.SimpleNamespace(),                                   # an old object with no field
    types.SimpleNamespace(outbound_notice_placement=None),
    types.SimpleNamespace(outbound_notice_placement="bogus"),
    types.SimpleNamespace(outbound_notice_placement=OWN_SENTENCE_FIRST),
    _decision(placement=OWN_SENTENCE_FIRST),
]


@pytest.mark.parametrize("decision, text, opener, pinned", list(itertools.product(
    _DECISIONS, _TEXTS, [None, "", "Hi, this is Sarah."], [False, True])))
def test_default_placement_equals_todays_rule(decision, text, opener, pinned):
    today = bool(opener and text and is_platform_notice_text(text))
    got = decide_notice_placement(decision, text, opener_available=bool(opener), pinned=pinned,
                                  call_id="c1", path="cascaded")
    assert got.in_opening is today


def test_normalize_placement_defaults_everything_unknown():
    assert [normalize_placement(v) for v in (None, "", "x", 3, IN_OPENING, OWN_SENTENCE_FIRST)] == [
        OWN_SENTENCE_FIRST, OWN_SENTENCE_FIRST, OWN_SENTENCE_FIRST, OWN_SENTENCE_FIRST,
        IN_OPENING, OWN_SENTENCE_FIRST]


# 2
async def test_account_choice_says_the_notice_inside_the_opening():
    spoken, session = await opener_t._answer(_decision(text=OWN))
    assert spoken == [("tts", opener_t.OPENER_RECORDED)]
    assert all(OWN not in str(e) for e in spoken)
    assert [m.content for m in session.conversation_history] == [opener_t.OPENER_RECORDED]
    assert rps.disclosure_delivered(opener_t.CALL_UUID)
    assert session._has_introduced is True


# 3
async def test_account_choice_interrupted_is_not_confirmed_and_not_restarted():
    spoken, _session = await opener_t._answer(_decision(text=OWN), interrupted=True)
    assert spoken == [("tts", opener_t.OPENER_RECORDED)]
    assert rps.get_disclosure_state(opener_t.CALL_UUID) == rps.DISCLOSURE_FAILED


# 4
async def _outcome(monkeypatch, row, decision, *, premade):
    status, evidence, mismatch, _ = row
    voice, session, gw, provider, _pipe = premade_t.make_voice(
        monkeypatch, gateway=premade_t.Gateway(status, evidence, mismatch=mismatch))
    await premade_t._ring(voice)
    if not premade:
        del voice._premade_opener_lines
    rps.clear_disclosure_state(premade_t.CALL)
    await premade_t._answer(decision, lambda: _speak_recording_disclosure(
        voice, opener_with_notice=premade_t.NOTICE_OPENER))
    receipt = session._tts_playback_receipt or {}
    return (
        rps.get_disclosure_state(premade_t.CALL),
        [m.content for m in session.conversation_history],
        getattr(session, "_has_introduced", None),
        getattr(voice, "_opener_spoken_as_notice", None),
        session._tts_delivery_status, receipt.get("status"), receipt.get("evidence"),
    )


@pytest.mark.parametrize("premade", [True, False])
@pytest.mark.parametrize("row", premade_t._ROWS)
async def test_account_choice_has_exactly_the_platform_proof(monkeypatch, row, premade):
    platform = await _outcome(monkeypatch, row, premade_t.PLATFORM, premade=premade)
    chosen = await _outcome(monkeypatch, row, _decision(text=OWN), premade=premade)
    assert chosen == platform
    assert chosen[0] == row[3] and chosen[3] is True


# 5
async def test_caller_first_account_choice_uses_the_premade_notice_opener(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    voice, s, gw, provider, _ = premade_t.make_voice(monkeypatch)
    await premade_t._ring(voice)
    n = len(provider.texts)
    assert await premade_t._answer(
        _decision(text=OWN), lambda: speak_disclosure_before_first_reply(s, "Hello?")) is True
    assert len(provider.texts) == n                       # no provider call after ringing
    assert "opener_premade_used call_id=" in caplog.text and "kind=notice_opener" in caplog.text
    assert [m.content for m in s.conversation_history] == ["Hello?", premade_t.NOTICE_OPENER]
    assert OWN not in provider.texts
    assert any("why=account_choice" in m for m in _lines(caplog))


# 6
async def test_non_pickup_first_words_still_say_the_accounts_own_sentence_then_the_plain_identity(
        monkeypatch, caplog):
    # The Settings page promise: with "in the opening", the account's own sentence is
    # still said on a call where the opening is not said, e.g. when the person's first
    # words are not a greeting. The identity sentence follows it, in its plain form.
    caplog.set_level(logging.INFO)
    voice, s, gw, provider, pipe = premade_t.make_voice(monkeypatch)
    await premade_t._ring(voice)
    await premade_t._answer(
        _decision(text=OWN),
        lambda: speak_disclosure_before_first_reply(s, "Who is this? Stop calling me.", opener=False))
    assert [c[0] for c in pipe.calls] == [OWN, IDENTITY_PLAIN]
    assert [m.content for m in s.conversation_history] == [OWN, IDENTITY_PLAIN]
    assert rps.get_disclosure_state(premade_t.CALL) == "transmitted"
    lines = _lines(caplog)
    assert len(lines) == 1 and "said=notice_then_identity_line" in lines[0]
    assert "why=account_choice_is_for_the_opening" in lines[0] and "stored=in_opening" in lines[0]


async def test_non_pickup_first_words_platform_wording_is_one_sentence_with_the_recorded_line(
        monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    voice, s, gw, provider, pipe = premade_t.make_voice(monkeypatch)
    await premade_t._ring(voice)
    await premade_t._answer(
        premade_t.PLATFORM,
        lambda: speak_disclosure_before_first_reply(s, "Who is this? Stop calling me.", opener=False))
    assert [c[0] for c in pipe.calls] == [premade_t.IDENTITY_NOTICE]
    lines = _lines(caplog)
    assert len(lines) == 1 and "said=identity_line_with_recorded_line" in lines[0]
    assert "why=platform_wording" in lines[0]


@pytest.mark.parametrize("decision, text, opener, pinned", list(itertools.product(
    _DECISIONS + [_decision(placement=IN_OPENING), _decision(text=rps.MINIMUM_DISCLOSURE_TEXT)],
    _TEXTS, [None, "", "Hi, this is Sarah."], [False, True])))
def test_carrier_opening_is_exactly_the_default(decision, text, opener, pinned):
    kw = dict(opener_available=bool(opener), pinned=pinned)
    assert placement_for(decision, text, carrier="opening", **kw) == placement_for(decision, text, **kw)
    got = decide_notice_placement(decision, text, call_id="c1", path="cascaded", carrier="opening", **kw)
    assert got == decide_notice_placement(decision, text, call_id="c1", path="cascaded", **kw)
    today = bool(opener and text and is_platform_notice_text(text))
    account_choice = (
        normalize_placement(getattr(decision, "outbound_notice_placement", None)) == IN_OPENING
        and bool(opener and text) and not pinned and not is_platform_notice_text(text)
    )
    assert got.in_opening is (today or account_choice)
    assert got.said in {"none", "notice_alone", "opening_with_recorded_line", "notice_then_opening"}


@pytest.mark.parametrize("stored", [None, OWN_SENTENCE_FIRST, IN_OPENING])
@pytest.mark.parametrize("switch", [True, False])
def test_identity_line_carrier_only_carries_the_platform_wording(monkeypatch, stored, switch):
    if not switch:
        monkeypatch.delenv(rt_t.on.ENV_SWITCH, raising=False)
        monkeypatch.setenv("OUTBOUND_NOTICE_PLACEMENT_CHOICE_ENABLED", "")
    decision = types.SimpleNamespace(outbound_notice_placement=stored)
    kw = dict(opener_available=True, pinned=False, carrier="identity_line")
    assert placement_for(decision, None, **kw) == NoticePlacement(False, "no_notice", "none")
    assert placement_for(decision, rps.MINIMUM_DISCLOSURE_TEXT, **kw) == NoticePlacement(
        True, "platform_wording", "identity_line_with_recorded_line")
    own = placement_for(decision, OWN, **kw)
    assert own.in_opening is False and own.said == "notice_then_identity_line"
    assert own.why == ("account_choice_is_for_the_opening" if stored == IN_OPENING else "account_sentence_first")


# 7
async def test_realtime_account_choice_requires_the_opening_with_recorded_line():
    h = rt_t.build()
    gate = await rt_t.start(h, _decision(text=OWN))
    assert gate.notice == "platform_in_opener"
    assert gate.required_text == rt_t.PLATFORM_OPENER and gate.notice_owed is True
    await rt_t.feed(h.bridge, f"{OWN} {rt_t.PLAIN_OPENER}", rt_t.PLATFORM_OPENER)
    h.rt.repair_unspoken_response.assert_awaited_once()      # own text + plain opener withheld
    assert len(h.sent) == 1
    assert rps.get_disclosure_state(rt_t.CALL) == "transmitted"


# 8
async def test_incoming_calls_ignore_the_choice(caplog):
    caplog.set_level(logging.INFO)
    log = []
    voice_session, _session = _make_voice_session(log)
    voice_session._first_speaker = "user"
    voice_session._inbound_admission["config_snapshot"]["inbound_config"][
        "consent_message"] = "This exact inbound call is recorded."
    voice_session.media_gateway = _GatedInboundMediaGateway(log)
    p_container, p_policy = _patched_policy(_decision(text=OWN))
    with p_container, p_policy:
        assert await prepare_inbound_recording(voice_session) is True
    heard = [value for kind, value in log if kind == "tts"]
    assert heard[0] == "This exact inbound call is recorded."
    rps.clear_disclosure_state(DISC_CALL)

    h = rt_t.build(direction=Direction.INBOUND, greet=False, inbound=True)
    gate = await rt_t.start(h, _decision(text=OWN), greet=False)
    assert gate.notice == "not_recorded"
    assert rps.get_disclosure_state(rt_t.CALL) is None
    assert all("path=realtime" not in m for m in _lines(caplog))


def _row(mode="two_party", placement="__absent__", text=OWN, codes=()):
    row = {"default_consent_mode": mode, "retention_days": 90, "announcement_text": text,
           "opt_out_dtmf_digit": None, "two_party_country_codes": list(codes)}
    if placement != "__absent__":
        row["outbound_notice_placement"] = placement
    return row


async def _decide(row, callee=None):
    service = rps.RecordingPolicyService(object())
    with patch.object(rps.RecordingPolicyService, "_load_row", new=AsyncMock(return_value=row)):
        return await service.decide(tenant_id="t1", callee_country_code=callee)


# 9
async def test_no_notice_owed_is_unaffected():
    spoken, session = await opener_t._answer(_decision(required=False, text=None))
    assert spoken and all(kind == "presynth_audio" for kind, _ in spoken)
    assert [m.content for m in session.conversation_history] == [opener_t.OPENER]
    assert rps.get_disclosure_state(opener_t.CALL_UUID) == rps.DISCLOSURE_NOT_REQUIRED
    got = await _decide(_row(mode="one_party", placement=IN_OPENING), callee="JP")
    assert got.announcement_required is False and got.reason == "tenant_policy_one_party"


# 10
async def test_decide_reads_the_stored_choice():
    assert (await _decide(_row())).outbound_notice_placement == OWN_SENTENCE_FIRST      # key absent
    two = await _decide(_row(placement=IN_OPENING))
    assert two.outbound_notice_placement == IN_OPENING and two.announcement_required is True
    one = await _decide(_row(mode="one_party", placement=IN_OPENING), callee="GB")
    assert one.outbound_notice_placement == IN_OPENING and one.announcement_required is True
    assert (await _decide(_row(placement="bogus"))).outbound_notice_placement == OWN_SENTENCE_FIRST


# 11
async def test_one_placement_log_line_per_call(caplog):
    caplog.set_level(logging.INFO)
    await opener_t._answer(_decision(text=OWN, mode_reason="tenant_policy_two_party_announce"))
    lines = _lines(caplog)
    assert len(lines) == 1
    assert (f"call_id={opener_t.CALL_UUID[:12]} path=cascaded stored=in_opening choice_switch=on "
            "said=opening_with_recorded_line why=account_choice "
            "policy_reason=tenant_policy_two_party_announce") in lines[0]
    caplog.clear()
    h = rt_t.build()
    await rt_t.start(h, _decision(text=OWN))
    lines = _lines(caplog)
    assert len(lines) == 1
    assert "path=realtime" in lines[0] and "why=account_choice" in lines[0]
    assert f"call_id={rt_t.CALL[:12]}" in lines[0]
