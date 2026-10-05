"""Bounded output grammar controls for an explicit relationship denial."""
from copy import deepcopy
from pathlib import Path

import pytest

from app.domain.services.voice_pipeline.conversation_guards import (
    CustomerRelationship,
    contradicted_customer_claim,
)


@pytest.mark.parametrize("text", [
    "As our existing customer, your account is ready.",
    "As our client, you can use your account.",
    "As our existing merchant, your subscription is active.",
    "Thanks. As our existing customer, your account is ready.",
])
def test_direct_existing_relationship_presupposition_respects_denial(text):
    assert contradicted_customer_claim(text, relationship=CustomerRelationship.DENIED)


@pytest.mark.parametrize("text", [
    'The script says "As our existing customer, your account is ready."',
    'You said "You are an existing customer." That was incorrect.',
    "I won't describe you as our existing customer.",
    "I cannot confirm you are an existing customer.",
    "If you are an existing customer, support can help.",
    "If you join as our customer, we can discuss the next step.",
    "You are not our existing customer.",
    "We help existing customers with questions.",
    "Would you like to learn about our services?",
])
def test_quoted_negated_conditional_and_product_text_do_not_assert_relationship(text):
    assert contradicted_customer_claim(text, relationship=CustomerRelationship.DENIED) is None


@pytest.mark.parametrize("relationship", [CustomerRelationship.UNKNOWN, CustomerRelationship.AFFIRMED])
def test_no_denial_does_not_invent_a_relationship_correction(relationship):
    assert contradicted_customer_claim(
        "As our existing customer, your account is ready.", relationship=relationship,
    ) is None


@pytest.mark.parametrize("provider", ["openai", "xai"])
async def test_native_parser_and_playout_gate_reject_same_direct_claim(provider):
    from tests.qualification.ag04_native import NativeReplay, _corpus

    corpus = _corpus(Path(__file__).resolve().parents[3])
    case = deepcopy(next(c for c in corpus["scenarios"] if c["id"] == "native.customer_denial"))
    claim = "As our existing customer, your account is ready."
    case["steps"][1]["text"] = claim
    row = await NativeReplay(case, provider, corpus).run()
    assert row["raw_output"][0]["text"] == claim
    assert row["media"]["submissions"] == []
    assert row["submitted_speech"] == []
    assert all(finding["pass"] for finding in row["findings"]["control"])
