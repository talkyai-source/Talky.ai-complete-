import json

import pytest

from app.domain.services.support.bot.result import InvalidBotOutput, parse_final_result
from app.domain.services.support.bot.types import Source

SRC = {"a": Source("a", "Title A", "/docs/a")}


def raw(**kw):
    d = {"answer": "Hello there", "sources": [], "resolution_prompt": False, "needs_clarification": False,
         "propose_escalation": False, "escalation_reason": "", "category": "billing"}
    d.update(kw)
    return json.dumps(d)


def test_valid():
    r = parse_final_result(raw(sources=["a", "zzz"]), allowed_sources=SRC)
    assert r["sources"] == (SRC["a"],) and r["escalation_reason"] is None and r["category"] == "billing"


def test_unknown_category_and_reason_cleanup():
    r = parse_final_result(raw(category="hax", escalation_reason="a\x00b\n" + "x" * 500), allowed_sources={})
    assert r["category"] == "other" and len(r["escalation_reason"]) <= 200 and "\x00" not in r["escalation_reason"]


def test_answer_truncated_on_word():
    r = parse_final_result(raw(answer="word " * 500), allowed_sources={})
    assert len(r["answer"]) <= 1200 and r["answer"].endswith("word")


@pytest.mark.parametrize("bad", ["", "[]", "nope", raw(answer="  "), raw(propose_escalation="yes"),
                                 raw(resolution_prompt=1), raw(sources="a")])
def test_invalid(bad):
    with pytest.raises(InvalidBotOutput):
        parse_final_result(bad, allowed_sources=SRC)
