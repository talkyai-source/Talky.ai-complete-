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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source",
    # caller_stated is the one exception since 2026-09-28 — see below.
    ("agent_inferred", "imported", "manual_edit"),
)
async def test_contact_is_refused_until_machine_confirmation(source):
    svc = LeadCaptureService(pool=object())
    with pytest.raises(InvalidCaptureError, match="confirmed"):
        await svc.capture(
            tenant_id="t",
            call_id="c",
            field_key="email",
            value="bob@acme.com",
            source=source,
            field_type="email",
            confirmed=False,
            validation_status="awaiting_confirmation",
        )


class _RecordingConn:
    def __init__(self):
        self.args = None

    def transaction(self):
        return _CM(None)

    async def execute(self, *_a):
        return None

    async def fetchrow(self, _sql, *args):
        self.args = args
        return {"id": "row"}


class _CM:
    def __init__(self, v):
        self.v = v

    async def __aenter__(self):
        return self.v

    async def __aexit__(self, *_a):
        return None


class _RecordingPool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self, **_kw):
        return _CM(self.conn)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field_type", "value", "stored"),
    (("phone", "+1 415 555 2671", "+14155552671"), ("email", "Bob@Acme.com", "bob@acme.com")),
)
async def test_a_contact_the_caller_stated_is_stored_unconfirmed(field_type, value, stored):
    """2026-09-28 (owner): what the caller SAID is kept while the read-back is
    pending, as confirmed=FALSE — validated exactly like a confirmed value."""
    conn = _RecordingConn()
    svc = LeadCaptureService(pool=_RecordingPool(conn))
    assert await svc.capture(
        tenant_id="11111111-1111-1111-1111-111111111111",
        call_id="22222222-2222-2222-2222-222222222222",
        field_key=field_type, value=value, source="caller_stated",
        field_type=field_type, confirmed=False,
        validation_status="awaiting_confirmation",
    ) is True
    assert conn.args[6] == stored
    assert conn.args[8] is False
    assert conn.args[12] == "awaiting_confirmation"
    assert conn.args[13] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status", ("needs_clarification", "invalid", "cancelled"),
)
async def test_a_caller_contact_that_is_not_being_read_back_is_refused(status):
    svc = LeadCaptureService(pool=object())
    with pytest.raises(InvalidCaptureError, match="confirmed"):
        await svc.capture(
            tenant_id="t", call_id="c", field_key="phone", value="+14155552671",
            source="caller_stated", field_type="phone", confirmed=False,
            validation_status=status,
        )


@pytest.mark.asyncio
async def test_an_unconfirmed_caller_contact_is_still_validated():
    svc = LeadCaptureService(pool=object())
    with pytest.raises(InvalidCaptureError, match="E.164"):
        await svc.capture(
            tenant_id="t", call_id="c", field_key="phone", value="4155552671",
            source="caller_stated", field_type="phone", confirmed=False,
            validation_status="awaiting_confirmation",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field_type", "value", "message"),
    (
        ("phone", "4155552671", "E.164"),
        ("email", "bob..smith@example.com", "valid"),
    ),
)
async def test_public_capture_refuses_invalid_confirmed_contact_values(
    field_type,
    value,
    message,
):
    svc = LeadCaptureService(pool=object())
    with pytest.raises(InvalidCaptureError, match=message):
        await svc.capture(
            tenant_id="t",
            call_id="c",
            field_key=field_type,
            value=value,
            source="manual_edit",
            field_type=field_type,
            confirmed=True,
            validation_status="confirmed",
        )


def test_contact_audit_columns_are_written_and_returned():
    src = inspect.getsource(LeadCaptureService.capture)
    for column in (
        "raw_value",
        "normalized_value",
        "validation_status",
        "confirmed_at",
    ):
        assert column in src
    read_src = inspect.getsource(LeadCaptureService.details_for_call)
    for column in (
        "raw_value",
        "normalized_value",
        "validation_status",
        "confirmed_at",
    ):
        assert column in read_src


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
