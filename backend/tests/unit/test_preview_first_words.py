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


async def test_own_text_default_placement_is_sentence_first():
    d = _decision(text=OWN + " Press 9 to opt out of recording.")
    out = await _preview(d)
    assert out == {"notice_first": spoken_disclosure_text(d), "opening": PLAIN,
                   "notice_in_opening": False}
    assert "Press 9" not in out["notice_first"]


async def test_platform_wording_is_inside_the_opening():
    out = await _preview(_decision(text=PLATFORM))
    assert out == {"notice_first": None, "opening": RECORDED, "notice_in_opening": True}


async def test_stored_in_opening_follows_the_platform_switch(monkeypatch):
    d = _decision(placement="in_opening")
    monkeypatch.setenv(SWITCH, "1")
    assert await _preview(d) == {"notice_first": None, "opening": RECORDED, "notice_in_opening": True}
    monkeypatch.delenv(SWITCH)
    assert await _preview(d) == {"notice_first": OWN, "opening": PLAIN, "notice_in_opening": False}


async def test_no_notice_owed_is_the_plain_opening():
    assert await _preview(_decision(required=False, text=None)) == {
        "notice_first": None, "opening": PLAIN, "notice_in_opening": False}
    assert await _preview(_decision(required=False, text=None, record=False)) == {
        "notice_first": None, "opening": PLAIN, "notice_in_opening": False}


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


@pytest.mark.parametrize("decision, name", [
    (_decision(), "own"), (_decision(text=PLATFORM), "platform"),
    (_decision(required=False, text=None), "none"),
])
async def test_the_preview_equals_what_the_outgoing_call_speaks(monkeypatch, decision, name):
    from tests.unit import test_outbound_ai_disclosure_opener as h

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
_FW = {"notice_first": None, "opening": "x", "notice_in_opening": True}


async def test_route_sets_the_field_for_outbound_and_not_otherwise():
    from app.api.v1.endpoints.campaigns import preview_prompt
    target = "app.domain.services.first_words_preview.outbound_first_words"
    with patch(target, new=AsyncMock(return_value=_FW)) as m:
        out = await preview_prompt(_body(), current_user=_USER, db_client=_DB)
        assert out.outbound_first_words.model_dump() == _FW
        kw = m.await_args
        assert kw.args == (_DB.pool, "t1")
        assert kw.kwargs == {"agent_name": "Grace", "company_name": "Acme"}
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
