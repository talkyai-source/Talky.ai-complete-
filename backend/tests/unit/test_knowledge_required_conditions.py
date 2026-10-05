"""A budget must not turn a conditional price into an unconditional offer."""
import pytest

from app.domain.services.voice_pipeline.kb_budget import prepare_knowledge_evidence
from app.services.scripts.knowledge.passages import select_passage


FACT = "Starter costs £19 per month."
REQUIREMENTS = [
    "A twelve-month contract is required.",
    "Merchants must maintain an active bank account.",
    "A minimum order of ten devices applies.",
    "Approval is mandatory before activation.",
]


def long_source(requirement):
    return "General background information. " * 35 + FACT + " " + requirement


@pytest.mark.parametrize("requirement", REQUIREMENTS)
def test_following_requirement_stays_with_price_when_both_fit(requirement):
    expected = FACT + " " + requirement
    assert select_passage(long_source(requirement), "Starter price", len(expected)) == expected


@pytest.mark.parametrize("requirement", REQUIREMENTS)
def test_price_is_withheld_when_its_requirement_does_not_fit(requirement):
    assert select_passage(long_source(requirement), "Starter price", len(FACT)) == ""


@pytest.mark.parametrize("requirement", REQUIREMENTS)
def test_shared_voice_evidence_does_not_admit_price_without_requirement(requirement):
    evidence = prepare_knowledge_evidence([
        {"id": "starter", "heading": "Starter", "content": long_source(requirement),
         "coverage": 1.0, "source_id": "approved-source", "source_version": 3},
    ], "Starter price", chunk_chars=len(FACT))
    assert evidence == {"status": "no_match", "passages": [], "text": ""}


def test_complete_short_source_and_unconditional_price_remain_available():
    complete = FACT + " " + REQUIREMENTS[0]
    assert select_passage(complete, "Starter price", len(complete)) == complete
    source = "General background information. " * 35 + FACT
    assert select_passage(source, "Starter price", len(FACT)) == FACT


def test_multiple_adjacent_requirements_are_preserved_or_withheld_together():
    requirements = " ".join(REQUIREMENTS)
    expected = FACT + " " + requirements
    assert select_passage(long_source(requirements), "Starter price", len(expected)) == expected
    assert select_passage(long_source(requirements), "Starter price", len(expected) - 1) == ""
