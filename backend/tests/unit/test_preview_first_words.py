"""The campaign preview says what an outgoing recorded call says first."""
from __future__ import annotations

import itertools
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.domain.services import notice_placement as np
from app.domain.services import recording_policy_service as rps
from app.domain.services.first_words_preview import outbound_first_words
from app.domain.services.recording_policy_service import (
    RecordingDecision,
    spoken_disclosure_text,
)

SWITCH = "OUTBOUND_NOTICE_PLACEMENT_CHOICE_ENABLED"
OWN = "Acme records calls to train its team."
PLATFORM = ("This call may be recorded for quality and training purposes. "
            "Press 9 at any time to opt out of recording.")


def _decision(required=True, text=OWN, placement="own_sentence_first", record=True):
    return RecordingDecision(
        should_record=record, announcement_required=required, announcement_text=text,
        opt_out_dtmf_digit=None, retention_days=90, reason="test",
        outbound_notice_placement=placement,
    )


async def _preview(decision, agent="Sarah", company="All State Estimation"):
    with patch.object(rps.RecordingPolicyService, "decide", new=AsyncMock(return_value=decision)):
        return await outbound_first_words(object(), "tenant-1", agent_name=agent, company_name=company)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv(SWITCH, raising=False)
    rps._DISCLOSURE_LEDGER.clear()
    yield
    rps._DISCLOSURE_LEDGER.clear()


PLAIN = "Hi, this is Sarah, an AI assistant from All State Estimation. Who am I speaking with?"
RECORDED = ("Hi, this is Sarah, an AI assistant from All State Estimation, on a recorded line. "
            "Who am I speaking with?")


ALONE_RECORDED = "This is Sarah, an AI assistant from All State Estimation, on a recorded line."
ALONE_PLAIN = "This is Sarah, an AI assistant from All State Estimation."


async def test_own_text_default_placement_is_sentence_first():
    d = _decision(text=OWN + " Press 9 to opt out of recording.")
    out = await _preview(d)
    assert out == {"notice_first": spoken_disclosure_text(d), "opening": PLAIN,
                   "notice_in_opening": False, "waits_for_person": False,
                   "notice_alone": f"{spoken_disclosure_text(d)} {ALONE_PLAIN}", "notice_depends_on_place": False}
    assert "Press 9" not in out["notice_first"]


async def test_platform_wording_is_inside_the_opening():
    out = await _preview(_decision(text=PLATFORM))
    assert out == {"notice_first": None, "opening": RECORDED, "notice_in_opening": True,
                   "waits_for_person": False, "notice_alone": ALONE_RECORDED,
                   "notice_depends_on_place": False}


async def test_stored_in_opening_follows_the_platform_switch(monkeypatch):
    d = _decision(placement="in_opening")
    monkeypatch.setenv(SWITCH, "1")
    on = await _preview(d)
    assert (on["notice_first"], on["opening"], on["notice_in_opening"]) == (None, RECORDED, True)
    # The account's own sentence is still said on first words that are not a greeting.
    assert on["notice_alone"] == f"{OWN} {ALONE_PLAIN}"
    monkeypatch.delenv(SWITCH)
    off = await _preview(d)
    assert (off["notice_first"], off["opening"], off["notice_in_opening"]) == (OWN, PLAIN, False)
    assert off["notice_alone"] == f"{OWN} {ALONE_PLAIN}"


async def test_no_notice_owed_is_the_plain_opening():
    want = {"notice_first": None, "opening": PLAIN, "notice_in_opening": False,
            "waits_for_person": False, "notice_alone": ALONE_PLAIN, "notice_depends_on_place": False}
    assert await _preview(_decision(required=False, text=None)) == want
    assert await _preview(_decision(required=False, text=None, record=False)) == want


async def test_a_failing_policy_read_returns_none_with_one_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="app.domain.services.first_words_preview"):
        with patch.object(rps.RecordingPolicyService, "decide",
                          new=AsyncMock(side_effect=ValueError("secret detail"))):
            out = await outbound_first_words(object(), "t", agent_name="A", company_name="B")
    assert out is None
    msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert msgs == ["first_words_preview_unavailable error=ValueError"]
    assert await outbound_first_words(None, "t", agent_name="A", company_name="B") is None


async def test_the_preview_writes_no_placement_line_and_no_ledger_entry(caplog):
    with caplog.at_level(logging.DEBUG):
        await _preview(_decision())
        await _preview(_decision(text=PLATFORM))
    assert not [r for r in caplog.records if "recording_notice_placement" in r.getMessage()]
    assert rps._DISCLOSURE_LEDGER == {} or len(rps._DISCLOSURE_LEDGER) == 0


@pytest.mark.parametrize("decision, name, switch", [
    (_decision(), "own", None), (_decision(text=PLATFORM), "platform", None),
    (_decision(required=False, text=None), "none", None),
    (_decision(placement="in_opening"), "own_in_opening", "1"),
])
async def test_the_preview_equals_what_the_outgoing_call_speaks(monkeypatch, decision, name, switch):
    from tests.unit import test_outbound_ai_disclosure_opener as h

    if switch:
        monkeypatch.setenv(SWITCH, switch)

    orig = h._call_session
    monkeypatch.setattr(h, "_call_session", lambda first_name=None, **kw: orig(None, **kw))
    spoken, session = await h._answer(decision)
    out = await _preview(decision)
    history = [m.content for m in session.conversation_history]
    if out["notice_first"] is not None:
        assert spoken[0] == ("tts", out["notice_first"])
        assert history == [out["notice_first"], out["opening"]]
    elif out["notice_in_opening"]:
        assert spoken == [("tts", out["opening"])]
        assert history == [out["opening"]]
    else:
        assert history == [out["opening"]]
        assert all(kind == "presynth_audio" for kind, _ in spoken)


# ---- the route ----------------------------------------------------------

def _body(direction="outbound", mode=None):
    from app.api.v1.schemas.campaigns import CampaignPromptPreviewRequest
    from tests.unit.test_campaign_preview_prompt import LEAD_GEN_SLOTS
    kw = {"pipeline_mode": mode} if mode else {}
    return CampaignPromptPreviewRequest(
        persona_type="lead_gen", company_name="Acme", agent_name="Grace",
        campaign_slots=LEAD_GEN_SLOTS, direction=direction, **kw)


_USER = SimpleNamespace(id="u", tenant_id="t1")
_DB = SimpleNamespace(pool=object())
_FW = {"notice_first": None, "opening": "x", "notice_in_opening": True, "waits_for_person": True, "notice_alone": "n", "notice_depends_on_place": True}


async def test_route_sets_the_field_for_outbound_and_not_otherwise():
    from app.api.v1.endpoints.campaigns import preview_prompt
    target = "app.domain.services.first_words_preview.outbound_first_words"
    with patch(target, new=AsyncMock(return_value=_FW)) as m:
        out = await preview_prompt(_body(), current_user=_USER, db_client=_DB)
        assert out.outbound_first_words.model_dump() == _FW
        kw = m.await_args
        assert kw.args == (_DB.pool, "t1")
        assert kw.kwargs == {"agent_name": "Grace", "company_name": "Acme",
                             "opening_mode": "agent_first"}
        inbound = await preview_prompt(_body("inbound"), current_user=_USER, db_client=_DB)
        assert inbound.outbound_first_words is None
        rt = await preview_prompt(_body(mode="realtime"), current_user=_USER, db_client=_DB)
        assert rt.outbound_first_words is None
        assert m.await_count == 1


async def test_route_answer_is_unchanged_when_the_read_returns_none():
    from app.api.v1.endpoints.campaigns import preview_prompt
    target = "app.domain.services.first_words_preview.outbound_first_words"
    with patch(target, new=AsyncMock(return_value=_FW)):
        with_field = await preview_prompt(_body(), current_user=_USER, db_client=_DB)
    with patch(target, new=AsyncMock(return_value=None)):
        without = await preview_prompt(_body(), current_user=_USER, db_client=_DB)
    assert without.outbound_first_words is None
    assert without.model_dump(exclude={"outbound_first_words"}) == \
        with_field.model_dump(exclude={"outbound_first_words"})


# ---- placement_for ------------------------------------------------------

@pytest.mark.parametrize("text, placement, avail, pinned, switch", list(itertools.product(
    [None, OWN, PLATFORM], ["own_sentence_first", "in_opening", None],
    [True, False], [True, False], ["", "1"])))
def test_placement_for_equals_decide_and_logs_nothing(monkeypatch, caplog, text, placement, avail, pinned, switch):
    monkeypatch.setenv(SWITCH, switch)
    d = _decision(placement=placement)
    with caplog.at_level(logging.DEBUG, logger="app.domain.services.notice_placement"):
        pure = np.placement_for(d, text, opener_available=avail, pinned=pinned)
        assert not caplog.records
        full = np.decide_notice_placement(
            d, text, opener_available=avail, pinned=pinned, call_id="c1", path="cascaded")
    assert pure == full
    assert len(caplog.records) == 1


# ---- second round -------------------------------------------------------

from tests.unit.test_recording_policy import _BASE_ROW, _FakePool  # noqa: E402

UUID = "11111111-1111-4111-8111-111111111111"


class _BrokenPool:
    def acquire(self, *a, **k):
        raise ConnectionError("db down")


async def _real(pool, tenant=UUID, **kw):
    return await outbound_first_words(pool, tenant, agent_name="Sarah",
                                      company_name="All State Estimation", **kw)


@pytest.mark.parametrize("pool, tenant", [(_BrokenPool(), UUID), (_FakePool(None), "not-a-uuid")])
async def test_a_failed_policy_read_is_not_shown_as_a_notice(caplog, pool, tenant):
    with caplog.at_level(logging.INFO):
        out = await _real(pool, tenant)
    assert out is None
    warns = [r.getMessage() for r in caplog.records
             if r.name == "app.domain.services.first_words_preview"]
    assert warns == ["first_words_preview_unavailable error=policy_lookup_failed"]


async def test_an_absent_row_is_still_shown_as_the_default_notice():
    out = await _real(_FakePool(None))
    assert out is not None and out["notice_in_opening"] is True


async def test_waits_for_person_follows_the_opening_mode():
    row = {**_BASE_ROW, "announcement_text": OWN}
    assert (await _real(_FakePool(row), opening_mode="callee_first"))["waits_for_person"] is True
    assert (await _real(_FakePool(row), opening_mode="agent_first"))["waits_for_person"] is False
    assert (await _real(_FakePool(row)))["waits_for_person"] is False


async def test_notice_alone_is_the_sentence_said_without_an_opening():
    own = await _preview(_decision())
    assert own["notice_alone"] == f"{OWN} {ALONE_PLAIN}"
    plat = await _preview(_decision(text=PLATFORM))
    assert plat["notice_alone"] == ALONE_RECORDED
    assert "AI assistant" in plat["notice_alone"] and "Hi," not in plat["notice_alone"]
    assert (await _preview(_decision(required=False, text=None)))["notice_alone"] == ALONE_PLAIN


async def test_notice_depends_on_place_through_the_real_decide():
    one = {**_BASE_ROW, "default_consent_mode": "one_party", "announcement_text": OWN}
    two = {**_BASE_ROW, "default_consent_mode": "two_party", "announcement_text": OWN}
    off = {**_BASE_ROW, "default_consent_mode": "disabled"}
    d = await rps.RecordingPolicyService(_FakePool(one)).decide(tenant_id=UUID)
    assert d.reason == "tenant_policy_one_party_two_party_destination"
    out = await _real(_FakePool(one))
    assert out["notice_depends_on_place"] is True and out["notice_first"] == OWN
    assert (await _real(_FakePool(two)))["notice_depends_on_place"] is False
    off_out = await _real(_FakePool(off))
    assert off_out["notice_depends_on_place"] is False and off_out["notice_alone"] == (
        "This is Sarah, an AI assistant from All State Estimation.")


async def test_one_info_line_per_run_without_notice_text(caplog):
    with caplog.at_level(logging.INFO, logger="app.domain.services.first_words_preview"):
        await _preview(_decision(text=PLATFORM))
        await _preview(_decision())
        await _preview(_decision(required=False, text=None))
    # Only the preview's own lines: the policy functions it calls write lines of their
    # own (left as they are), and whether those are captured depends on the log level.
    lines = [r.getMessage() for r in caplog.records
             if r.name == "app.domain.services.first_words_preview"]
    assert lines == [
        "first_words_preview tenant=tenant-1 notice=in_opening waits_for_person=false",
        "first_words_preview tenant=tenant-1 notice=first waits_for_person=false",
        "first_words_preview tenant=tenant-1 notice=none waits_for_person=false",
    ]
    assert not any("recorded" in m or "Acme" in m for m in lines)


@pytest.mark.parametrize("decision", [_decision(), _decision(text=PLATFORM),
                                      _decision(required=False, text=None)])
async def test_non_greeting_first_words_say_the_notice_alone_and_no_opening(decision):
    import types

    from app.domain.services.voice_pipeline.caller_first_disclosure import (
        speak_disclosure_before_first_reply,
    )
    from tests.unit import test_outbound_ai_disclosure_opener as h

    log = h._Log()
    session = h._call_session(None)
    voice_session = types.SimpleNamespace(
        call_id=h.CALL_UUID, call_session=session,
        pipeline=h._Pipeline(log), media_gateway=h._Gateway(log), tts_provider=h._Tts(log),
        config=types.SimpleNamespace(voice_id="v", tts_sample_rate=8000),
    )
    session._voice_session_ref = voice_session
    container = types.SimpleNamespace(is_initialized=True, db_pool=object())
    with patch("app.core.container.get_container", return_value=container), patch.object(
        rps.RecordingPolicyService, "decide", new=AsyncMock(return_value=decision),
    ):
        said_opener = await speak_disclosure_before_first_reply(
            session, "I am busy, what is this about")
    out = await _preview(decision)
    assert said_opener is False
    # 23 Nov 2026: what the preview shows is what the call says, sentence for sentence.
    assert " ".join(e[1] for e in log.events if e[0] == "tts") == out["notice_alone"]
    assert all(m.content != out["opening"] for m in session.conversation_history)


@pytest.mark.parametrize("switch", [None, "1"])
@pytest.mark.parametrize("decision", [
    _decision(text=PLATFORM),
    _decision(),
    _decision(placement="in_opening"),
    _decision(required=False, text=None),
    _decision(required=False, text=None, record=False),
], ids=["platform", "own_default", "own_in_opening", "no_notice_owed", "recording_off"])
async def test_notice_alone_is_what_the_call_really_speaks_on_a_non_greeting_turn(
        monkeypatch, decision, switch):
    from tests.unit import test_premade_caller_first_opener as pm
    from app.domain.services.voice_pipeline.caller_first_disclosure import (
        speak_disclosure_before_first_reply,
    )

    if switch:
        monkeypatch.setenv(SWITCH, switch)
    voice, session, _gw, _provider, pipe = pm.make_voice(monkeypatch)
    await pm._ring(voice)
    said_opener = await pm._answer(
        decision, lambda: speak_disclosure_before_first_reply(session, "I am busy, what is this about"))
    out = await _preview(decision)
    assert said_opener is False
    assert " ".join(c[0] for c in pipe.calls) == out["notice_alone"]
