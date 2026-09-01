"""Lead capture: provenance, and the rule that stops a guess overwriting a fact.

goals.md §7 asks for the source of every value and says inferred values are not
confirmed facts. The consequence nobody writes down is what happens when the
same field is captured twice on one call — which happens constantly, because a
model that hears "next quarter, probably" after a firm date will happily offer
the vaguer one.

These tests pin the pure logic. The trust comparison itself runs in SQL
(`array_position`) so it holds under a race, and is covered by
`test_capture_ordering_is_enforced_in_sql` reading the statement rather than
mocking a database into agreeing with itself.
"""
from __future__ import annotations

import inspect
import json

import pytest

from app.domain.services import lead_capture_service as mod
from app.domain.services.lead_capture_service import (
    TRUST_ORDER,
    InvalidCaptureError,
    LeadCaptureService,
    normalise_value,
)


# ── trust ordering ──────────────────────────────────────────────────────────

def test_a_human_edit_outranks_everything():
    assert TRUST_ORDER[-1] == "manual_edit"


def test_a_model_guess_is_the_least_trusted_thing():
    """An inference is the one source that is explicitly NOT a fact (§7), so it
    must never displace anything else."""
    assert TRUST_ORDER[0] == "agent_inferred"


def test_what_the_caller_said_beats_what_the_model_guessed():
    assert TRUST_ORDER.index("caller_stated") > TRUST_ORDER.index("agent_inferred")


def test_what_the_caller_said_beats_the_imported_record():
    """The CSV said the company was Acme; on the call they said they left Acme
    two years ago. The person on the phone is the better source."""
    assert TRUST_ORDER.index("caller_stated") > TRUST_ORDER.index("imported")


def test_capture_ordering_is_enforced_in_sql_not_in_python():
    """THE REGRESSION THIS FILE EXISTS FOR.

    The first draft compared the incoming rank against a hardcoded 0, which
    meant every write won and the whole trust rule was decorative. Doing the
    comparison in the statement is also what makes it safe under two concurrent
    writers.
    """
    src = inspect.getsource(LeadCaptureService.capture)
    assert "array_position" in src, (
        "the trust comparison must happen in SQL against the row present at "
        "write time, not against a rank read earlier in Python"
    )
    assert "call_lead_details.source" in src, (
        "the comparison must reference the EXISTING row's source"
    )


def test_confirmed_is_sticky():
    """Once a caller has agreed a value, a later unconfirmed write of the same
    value must not silently downgrade it."""
    src = inspect.getsource(LeadCaptureService.capture)
    assert "call_lead_details.confirmed OR EXCLUDED.confirmed" in src


def test_an_unconfirmed_write_cannot_replace_a_confirmed_value():
    """Sticky ``confirmed`` alone was a trap: a same-source unconfirmed retry
    still passed the trust WHERE, overwrote ``value``, and inherited
    confirmed=TRUE — a confirmed-looking row holding a value nobody agreed.
    The statement must refuse that write."""
    src = inspect.getsource(LeadCaptureService.capture)
    assert "NOT (call_lead_details.confirmed AND NOT EXCLUDED.confirmed)" in src


# ── value normalisation ─────────────────────────────────────────────────────

def test_multi_select_becomes_a_json_array():
    assert json.loads(normalise_value(["a", "b"], "multi_select")) == ["a", "b"]


def test_multi_select_accepts_a_bare_value():
    assert json.loads(normalise_value("solo", "multi_select")) == ["solo"]


def test_blank_values_become_none_so_they_read_as_declined():
    """A NULL value means "asked, and the caller declined". An ABSENT row means
    "never established". Both are legitimate; collapsing them loses the
    difference permanently, so an empty string must not become ''."""
    assert normalise_value("   ", "text") is None
    assert normalise_value(None, "text") is None
    assert normalise_value([], "multi_select") is None


def test_whitespace_is_trimmed_but_content_is_not_altered():
    assert normalise_value("  Acme Roofing Ltd  ", "text") == "Acme Roofing Ltd"


def test_an_overlong_value_is_refused_rather_than_truncated():
    """Truncating would put half a sentence in a CRM with no sign it was cut."""
    with pytest.raises(InvalidCaptureError):
        normalise_value("x" * (mod.MAX_VALUE_CHARS + 1), "notes")


# ── input validation ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_an_unknown_source_is_refused():
    svc = LeadCaptureService(pool=object())
    with pytest.raises(InvalidCaptureError, match="unknown source"):
        await svc.capture(
            tenant_id="t", call_id="c", field_key="budget",
            value="10k", source="vibes",
        )


@pytest.mark.asyncio
async def test_an_unknown_field_type_is_refused():
    svc = LeadCaptureService(pool=object())
    with pytest.raises(InvalidCaptureError, match="unknown field type"):
        await svc.capture(
            tenant_id="t", call_id="c", field_key="budget",
            value="10k", source="caller_stated", field_type="freeform",
        )


@pytest.mark.asyncio
async def test_an_empty_field_key_is_refused():
    svc = LeadCaptureService(pool=object())
    with pytest.raises(InvalidCaptureError, match="field_key is required"):
        await svc.capture(
            tenant_id="t", call_id="c", field_key="   ",
            value="10k", source="caller_stated",
        )


# ── bulk ────────────────────────────────────────────────────────────────────

def test_bulk_capture_does_not_lose_good_fields_to_one_bad_one():
    """A call that produced five usable values and one overlong note should
    keep the five. The loop catches per-item rather than aborting."""
    src = inspect.getsource(LeadCaptureService.capture_many)
    assert "except InvalidCaptureError" in src
    assert "continue" in src or "written +=" in src


def test_a_refused_write_is_logged_not_swallowed():
    """Silently dropping a capture is how you end up unable to explain a
    missing field weeks later."""
    src = inspect.getsource(LeadCaptureService.capture)
    assert "lead_capture_skipped" in src
