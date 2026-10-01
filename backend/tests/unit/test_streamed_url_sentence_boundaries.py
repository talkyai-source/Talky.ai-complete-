"""Uppercase URL suffixes need explicit address context, not a TLD catalog."""
import pytest

from app.domain.services.voice_pipeline.grounded_links import (
    grounded_url_hosts,
    ground_spoken_links,
)
from app.domain.services.voice_pipeline.sentence_segmentation import (
    find_sentence_end,
)


@pytest.mark.parametrize("address", [
    "https://EXAMPLE.COM/safe", "http://EXAMPLE.COM/safe", "www.EXAMPLE.COM/safe",
    "“https://EXAMPLE.COM/safe”,", "(https://EXAMPLE.COM/safe)",
])
def test_explicit_address_context_preserves_internal_uppercase_periods(address):
    text = f"Visit {address} now. Next thought."
    assert find_sentence_end(text) == text.index(". Next")


@pytest.mark.parametrize("fragment", ["EXAMPLE.C", "EXAMPLE.CO", "EXAMPLE.COM", "EXAMPLE.COM/safe"])
def test_verified_host_is_protected_while_suffix_arrives(fragment):
    text = "Visit " + fragment
    known_hosts = grounded_url_hosts(["Approved address: https://example.com/safe"])
    assert find_sentence_end(text, known_hosts=known_hosts) == -1


@pytest.mark.parametrize("address,hosts", [
    ("https://EXAMPLE.COM/safe", ()),
    ("EXAMPLE.COM/safe", ("example.com",)),
])
def test_real_sentence_period_after_address_is_still_a_boundary(address, hosts):
    text = "Visit " + address + "."
    assert find_sentence_end(text, known_hosts=hosts) == len(text) - 1
    assert find_sentence_end(text + " Next.", known_hosts=hosts) == len(text) - 1


@pytest.mark.parametrize("text,terminator", [
    ("That sounds right.Yes.Could you confirm?", "."),
    ("Is that right?Yes.Please continue.", "?"),
    ("Thank you!Yes.Please continue.", "!"),
    ("Visit unknown.COM/safe now.", "."),
])
def test_unverified_ambiguous_english_and_question_exclamation_defenses_remain(text, terminator):
    assert find_sentence_end(text, known_hosts={"example.com"}) == text.index(terminator)


def test_known_host_does_not_protect_an_unrelated_prefix():
    text = "Visit anotherexample.COM/safe now."
    assert find_sentence_end(text, known_hosts={"example.com"}) == text.index(".")


def test_early_clause_with_known_uppercase_url_does_not_count_as_complete_sentence():
    text = "Please review the current information at EXAMPLE.COM/safe, and then tell me which details you need"
    hosts = {"example.com"}
    assert find_sentence_end(text, allow_clause=True, known_hosts=hosts) == text.index(",")
    assert find_sentence_end(text, allow_clause=False, known_hosts=hosts) == -1


def test_extractor_reuses_factual_url_normalization_without_approving_generated_path():
    grounding = ["Our website is https://EXAMPLE.COM/safe.", "Other site: docs.example.org."]
    hosts = grounded_url_hosts(grounding)
    assert hosts == {"example.com", "docs.example.org"}
    text = "Visit EXAMPLE.COM/invented now."
    assert find_sentence_end(text, known_hosts=hosts) == len(text) - 1
    repaired, changed = ground_spoken_links(text, grounding)
    assert repaired == "Visit EXAMPLE.COM now."
    assert changed == ["example.com/invented"]


def test_no_factual_addresses_yields_no_host_exemptions():
    assert grounded_url_hosts(["Use a short answer.", "Contact anna@example.com.", None]) == frozenset()
