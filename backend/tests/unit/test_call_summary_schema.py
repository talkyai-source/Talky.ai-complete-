"""The summary schema must describe the JSON the prompt actually asks for.

Production, 2026-09-22:

    400 json_validate_failed — '/action_items/0' does not validate with
    /properties/action_items/items/type: expected string, but got object

_SYSTEM_PROMPT asks for ``action_items`` and ``objections`` as arrays of
objects. The schema derived every list key as an array of strings, so the model
obeyed the prompt and the provider threw the whole summary away. Only ~26 of 84
calls in 30 days kept a summary -- and because lead qualification reads the
summary, those calls never got a lead decision either.

The old comment claimed the schema "cannot drift" from EMPTY_SUMMARY. It could
not drift from the *defaults*; it drifted from the *prompt*, which is the thing
the model is actually following. These tests close that gap by reading the
prompt itself.
"""
import re

import pytest

from app.domain.services.call_summary.summarizer import (
    EMPTY_SUMMARY,
    _OBJECT_LIST_ITEMS,
    _SUMMARY_SCHEMA_PROPERTIES,
    _SYSTEM_PROMPT,
    _coerce,
)


def _keys_the_prompt_shows_as_object_lists() -> set:
    """Keys whose prompt example is a list whose first element is an object."""
    found = set()
    for line in _SYSTEM_PROMPT.splitlines():
        m = re.match(r'\s*"(?P<key>[a-z_]+)":\s*\[\s*(?P<first>.)', line)
        if m and m.group("first") == "{":
            found.add(m.group("key"))
    return found


def test_every_object_list_in_the_prompt_is_an_object_in_the_schema():
    """The regression itself: prompt says object, schema said string."""
    for key in _keys_the_prompt_shows_as_object_lists():
        prop = _SUMMARY_SCHEMA_PROPERTIES[key]
        assert prop["type"] == "array", key
        assert prop["items"]["type"] == "object", (
            f"{key}: the prompt asks for objects but the schema declares "
            f"{prop['items']['type']} — the provider will reject every summary "
            f"containing a {key} entry"
        )


def test_the_object_shapes_are_declared_for_exactly_those_keys():
    """No stale entry, and none missing — both directions."""
    assert set(_OBJECT_LIST_ITEMS) == _keys_the_prompt_shows_as_object_lists()


def test_action_items_and_objections_are_the_two_known_object_lists():
    """Pins the specific keys, so removing one from the prompt fails loudly."""
    assert _keys_the_prompt_shows_as_object_lists() == {"action_items", "objections"}


def test_scalar_keys_stay_strings_and_plain_lists_stay_string_arrays():
    assert _SUMMARY_SCHEMA_PROPERTIES["headline"] == {"type": "string"}
    assert _SUMMARY_SCHEMA_PROPERTIES["key_points"] == {
        "type": "array",
        "items": {"type": "string"},
    }


def test_schema_covers_exactly_the_summary_keys():
    assert set(_SUMMARY_SCHEMA_PROPERTIES) == set(EMPTY_SUMMARY)


@pytest.mark.parametrize("key", ["action_items", "objections"])
def test_object_items_survive_coercion_unchanged(key):
    """store.py reads these by truthiness, so the objects must reach it intact."""
    payload = {key: [{"item": "send the sample", "owner": "agent"}]}
    assert _coerce(payload)[key] == payload[key]


_JSON_TYPES = {
    "string": str,
    "object": dict,
    "array": list,
}


def _validate(instance, properties):
    """Just enough JSON-Schema to check item types — no new dependency."""
    for key, value in instance.items():
        prop = properties[key]
        assert isinstance(value, _JSON_TYPES[prop["type"]]), key
        if prop["type"] != "array":
            continue
        item_schema = prop["items"]
        for element in value:
            assert isinstance(element, _JSON_TYPES[item_schema["type"]]), (
                f"{key}: schema declares items as {item_schema['type']}, "
                f"got {type(element).__name__}"
            )
            if item_schema["type"] == "object":
                assert set(element) == set(item_schema["required"]), key


def test_the_exact_payload_production_rejected_now_validates():
    """The failed_generation from the 2026-09-16 400, in the shape it had.

    Under the old schema this raised on /action_items/0 — expected string,
    got object — and the whole summary was discarded.
    """
    generated = {
        "headline": "User unable to log in to bank visa app; contact details provided",
        "outcome": "no_interest",
        "qualification_status": "unqualified",
        "action_items": [{"item": "call the bank's support line", "owner": "caller"}],
        "objections": [{"objection": "already with another provider", "handled": "unresolved"}],
        "key_points": ["caller could not log in"],
    }
    _validate(generated, _SUMMARY_SCHEMA_PROPERTIES)


def test_the_old_string_schema_would_have_rejected_that_payload():
    """Proves the test above is actually load-bearing, not vacuous."""
    old_style = dict(_SUMMARY_SCHEMA_PROPERTIES)
    old_style["action_items"] = {"type": "array", "items": {"type": "string"}}
    with pytest.raises(AssertionError, match="action_items"):
        _validate(
            {"action_items": [{"item": "x", "owner": "agent"}]},
            old_style,
        )
