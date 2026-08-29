"""goals.md §7 — the live call must actually WRITE what it learned.

THE BUG THIS FILE EXISTS FOR
----------------------------
`LeadCaptureService.capture` had exactly two callers: the manual edit form and
itself. Nothing on the voice path ever called it, so `call_lead_details` was
empty for every call ever placed and the interested-lead panel, its badge and
its missing-required banner rendered an empty state forever. The service was
correct; it was simply never wired to a live call.

THE TRAP THIS FILE GUARDS
-------------------------
This repo has repeatedly shipped guards wired to a signal that is CONSTANT in
production. So the tests below pin BOTH directions of every gate:

  fires                                    does NOT fire
  -----------------------------------      ---------------------------------
  a turn that established a fact           a turn that established nothing
  a real campaign call                     a call flagged is_test
  a value that changed                     the same value seen again
  a confirmation upgrade                   an unchanged confirmed flag
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.services.voice_pipeline.lead_slot_capture import (
    CAPTURE_SOURCE,
    capture_session_slots,
    capture_turn_slots,
    resolve_call_binding,
    snapshot_slots,
)
from app.services.scripts import CallState

TENANT = "11111111-1111-1111-1111-111111111111"
CALL = "22222222-2222-2222-2222-222222222222"
CAMPAIGN = "33333333-3333-3333-3333-333333333333"
LEAD = "44444444-4444-4444-4444-444444444444"

# Positional parameter layout of LeadCaptureService.capture's INSERT.
A_TENANT, A_CALL, A_CAMPAIGN, A_LEAD = 0, 1, 2, 3
A_KEY, A_TYPE, A_VALUE, A_SOURCE, A_CONFIRMED = 4, 5, 6, 7, 8


class _AsyncCM:
    def __init__(self, target):
        self._target = target

    async def __aenter__(self):
        return self._target

    async def __aexit__(self, *a):
        return None


class _FakeConn:
    """Records every statement so the tests can assert on real SQL rather than
    on a mock agreeing with itself."""

    def __init__(self, *, is_test=False, call_row_exists=True, fetchrow_exc=None):
        self.statements: list[tuple[str, tuple]] = []
        self.executed: list[str] = []
        self._is_test = is_test
        self._call_row_exists = call_row_exists
        self._fetchrow_exc = fetchrow_exc

    def transaction(self):
        return _AsyncCM(None)

    async def execute(self, sql, *args):
        self.executed.append(sql)

    async def fetchrow(self, sql, *args):
        self.statements.append((sql, args))
        if self._fetchrow_exc is not None:
            raise self._fetchrow_exc
        if "FROM calls" in sql:
            return {"is_test": self._is_test} if self._call_row_exists else None
        return {"id": str(uuid.uuid4())}

    # -- convenience --------------------------------------------------------
    @property
    def inserts(self):
        return [(s, a) for s, a in self.statements if "call_lead_details" in s]

    @property
    def is_test_lookups(self):
        return [(s, a) for s, a in self.statements if "FROM calls" in s]


class _FakePool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self, **kwargs):
        return _AsyncCM(self.conn)


def _session(**slots) -> SimpleNamespace:
    return SimpleNamespace(call_id=CALL, captured_slots=CallState(**slots))


async def _flush(session, conn, **kwargs):
    return await capture_session_slots(
        session,
        pool=_FakePool(conn),
        call_id=CALL,
        tenant_id=TENANT,
        campaign_id=kwargs.pop("campaign_id", CAMPAIGN),
        lead_id=kwargs.pop("lead_id", LEAD),
        **kwargs,
    )


# -- the snapshot: what counts as an established fact -------------------------

def test_a_fresh_call_has_established_nothing():
    """ABSENT IS NOT NULL (§7): a slot the caller never filled produces no
    entry at all, so no row is written and the field reads as 'unknown'."""
    assert snapshot_slots(CallState()) == {}


def test_a_stated_email_is_an_established_fact():
    snap = snapshot_slots(CallState(email="dana@acme.co", email_confirmed=True))
    assert snap["email"]["value"] == "dana@acme.co"
    assert snap["email"]["confirmed"] is True
    assert snap["email"]["field_type"] == "email"


def test_an_unconfirmed_value_is_captured_but_not_marked_confirmed():
    """§7: 'Do not treat inferred values as confirmed facts.' An email the
    caller said but has not yet agreed on read-back is still worth capturing —
    it just must not claim to be settled."""
    snap = snapshot_slots(CallState(email="dana@acme.co", email_confirmed=False))
    assert snap["email"]["confirmed"] is False


def test_a_yes_no_slot_is_stored_as_words_not_a_python_bool():
    """`str(False)` in a CRM field reads as the string 'False'. Store the
    answer the caller actually gave."""
    assert snapshot_slots(CallState(bidding_active=False))["bidding_active"]["value"] == "no"
    assert snapshot_slots(CallState(bidding_active=True))["bidding_active"]["value"] == "yes"


def test_a_missing_slot_store_is_not_an_error():
    assert snapshot_slots(None) == {}


# -- the write: it fires ------------------------------------------------------

@pytest.mark.asyncio
async def test_a_call_with_stated_facts_writes_them_with_caller_provenance():
    """THE P0. Before this wiring the count here was 0 for every call ever
    placed."""
    conn = _FakeConn()
    session = _session(email="dana@acme.co", email_confirmed=True, follow_up="tuesday")

    written = await _flush(session, conn)

    assert written == 2
    keys = {args[A_KEY] for _sql, args in conn.inserts}
    assert keys == {"email", "follow_up"}
    for _sql, args in conn.inserts:
        assert args[A_SOURCE] == CAPTURE_SOURCE == "caller_stated"
        assert args[A_TENANT] == TENANT
        assert args[A_CALL] == CALL
        assert args[A_CAMPAIGN] == CAMPAIGN
        assert args[A_LEAD] == LEAD
    email_args = next(a for _s, a in conn.inserts if a[A_KEY] == "email")
    assert email_args[A_VALUE] == "dana@acme.co"
    assert email_args[A_CONFIRMED] is True


@pytest.mark.asyncio
async def test_a_phone_number_is_captured_too():
    conn = _FakeConn()
    session = _session(phone="+447700900123", phone_confirmed=True)

    assert await _flush(session, conn) == 1
    args = conn.inserts[0][1]
    assert args[A_KEY] == "phone"
    assert args[A_TYPE] == "phone"
    assert args[A_CONFIRMED] is True


# -- the write: it correctly does NOT fire ------------------------------------

@pytest.mark.asyncio
async def test_a_turn_that_established_nothing_writes_nothing():
    """THE CONSTANT-SIGNAL GUARD. If this test could not be made to fail by
    the test above, the trigger would be constant and the feature decorative."""
    conn = _FakeConn()

    assert await _flush(_session(), conn) == 0
    assert conn.statements == [], (
        "a call with no established facts must not even touch the database"
    )


@pytest.mark.asyncio
async def test_a_test_call_is_never_captured():
    """campaign_test_ws inserts a real calls row flagged is_test. Its slots
    must not pollute the tenant's lead data."""
    conn = _FakeConn(is_test=True)
    session = _session(email="dana@acme.co", email_confirmed=True)

    assert await _flush(session, conn) == 0
    assert conn.is_test_lookups, "the is_test flag must actually be read"
    assert conn.inserts == []


@pytest.mark.asyncio
async def test_a_real_call_is_captured_so_the_is_test_gate_varies():
    conn = _FakeConn(is_test=False)
    session = _session(email="dana@acme.co", email_confirmed=True)

    assert await _flush(session, conn) == 1


@pytest.mark.asyncio
async def test_no_matching_calls_row_writes_nothing():
    """A voice session with no dialer row (browser / ask_ai) has nothing to
    hang a lead detail off — call_lead_details.call_id is a FK to calls(id)."""
    conn = _FakeConn(call_row_exists=False)
    session = _session(email="dana@acme.co", email_confirmed=True)

    assert await _flush(session, conn) == 0
    assert conn.inserts == []


@pytest.mark.asyncio
async def test_an_unresolvable_call_id_writes_nothing():
    conn = _FakeConn()
    session = _session(email="dana@acme.co")

    written = await capture_session_slots(
        session, pool=_FakePool(conn), call_id=None, tenant_id=TENANT
    )
    assert written == 0
    assert conn.statements == []


# -- idempotency --------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_same_fact_is_not_rewritten_every_turn():
    """capture() is idempotent in SQL (ON CONFLICT), but re-issuing the same
    INSERT on every one of a 40-turn call is 40 pointless round trips on the
    latency-critical path."""
    conn = _FakeConn()
    session = _session(email="dana@acme.co", email_confirmed=True)

    assert await _flush(session, conn) == 1
    before = len(conn.inserts)
    assert await _flush(session, conn) == 0
    assert len(conn.inserts) == before


@pytest.mark.asyncio
async def test_a_later_confirmation_is_written_even_though_the_value_is_unchanged():
    """The read-back loop confirms an email several turns after it was heard.
    Same string, different fact — it must reach the database."""
    conn = _FakeConn()
    session = _session(email="dana@acme.co", email_confirmed=False)

    assert await _flush(session, conn) == 1
    assert conn.inserts[0][1][A_CONFIRMED] is False

    session.captured_slots = CallState(email="dana@acme.co", email_confirmed=True)
    assert await _flush(session, conn) == 1
    assert conn.inserts[-1][1][A_CONFIRMED] is True


@pytest.mark.asyncio
async def test_a_corrected_value_is_written():
    conn = _FakeConn()
    session = _session(email="wrong@acme.co")
    await _flush(session, conn)

    session.captured_slots = CallState(email="right@acme.co")
    assert await _flush(session, conn) == 1
    assert conn.inserts[-1][1][A_VALUE] == "right@acme.co"


@pytest.mark.asyncio
async def test_the_is_test_flag_is_read_once_per_call_not_once_per_turn():
    conn = _FakeConn()
    session = _session(email="a@b.co")
    await _flush(session, conn)
    session.captured_slots = CallState(email="a@b.co", follow_up="friday")
    await _flush(session, conn)

    assert len(conn.is_test_lookups) == 1


# -- it must never reach the call path ----------------------------------------

@pytest.mark.asyncio
async def test_a_database_failure_does_not_propagate_into_the_call():
    """A capture failure that raised here would tear down a LIVE call. The
    lead form is worth strictly less than the conversation."""
    conn = _FakeConn(fetchrow_exc=RuntimeError("connection reset by peer"))
    session = _session(email="dana@acme.co")

    assert await _flush(session, conn) == 0


@pytest.mark.asyncio
async def test_a_broken_pool_does_not_propagate_into_the_call():
    pool = MagicMock()
    pool.acquire = MagicMock(side_effect=RuntimeError("pool exhausted"))
    session = _session(email="dana@acme.co")

    assert await capture_session_slots(
        session, pool=pool, call_id=CALL, tenant_id=TENANT
    ) == 0


@pytest.mark.asyncio
async def test_one_bad_field_does_not_lose_the_others():
    """An overlong note must not cost the call its email."""
    conn = _FakeConn()
    session = _session(email="dana@acme.co", follow_up="x" * 5000)

    assert await _flush(session, conn) == 1
    assert conn.inserts[0][1][A_KEY] == "email"


# -- tenant scoping (prod's app role is superuser + BYPASSRLS) ----------------

@pytest.mark.asyncio
async def test_the_is_test_lookup_names_the_tenant_explicitly():
    """RLS is inert in production, so every statement carries its own
    tenant predicate."""
    conn = _FakeConn()
    await _flush(_session(email="a@b.co"), conn)

    sql, args = conn.is_test_lookups[0]
    assert "tenant_id = $2::uuid" in sql
    assert args[1] == TENANT


def test_the_capture_update_is_tenant_scoped():
    import inspect

    from app.domain.services.lead_capture_service import LeadCaptureService

    src = inspect.getsource(LeadCaptureService.capture)
    assert "call_lead_details.tenant_id = $1::uuid" in src, (
        "prod's app role is superuser+BYPASSRLS, so the ON CONFLICT update "
        "must name the tenant rather than trusting a policy"
    )


# -- teardown flush: facts established after the last completed turn ----------

def _hangup_fixture(slots, **conn_kwargs):
    conn = _FakeConn(**conn_kwargs)
    call_session = SimpleNamespace(
        call_id="voice-session-uuid",
        talklee_call_id="tlk_abc",
        tenant_id=TENANT,
        captured_slots=slots,
    )
    vs = SimpleNamespace(call_id="voice-session-uuid", call_session=call_session)
    vs._dialer_call_id = CALL
    vs._dialer_tenant_id = TENANT
    vs._dialer_campaign_id = CAMPAIGN
    vs._dialer_lead_id = LEAD

    svc = MagicMock()
    svc.get_transcript_json.return_value = [{"role": "user", "content": "hi"}]
    svc.get_transcript_text.return_value = "User: hi"
    svc.get_metrics.return_value = {}
    svc.clear_buffer = MagicMock()
    return conn, vs, svc


@pytest.mark.asyncio
async def test_teardown_flushes_facts_the_turn_loop_never_persisted():
    """The caller gives their email and the line drops before the agent's
    reply completes. The turn never finished, so the per-turn flush never ran."""
    from app.services.scripts.call_transcript_persister import (
        save_call_transcript_on_hangup,
    )

    conn, vs, svc = _hangup_fixture(
        CallState(email="late@acme.co", email_confirmed=True)
    )

    await save_call_transcript_on_hangup(
        voice_session=vs, transcript_service=svc, db_pool=_FakePool(conn)
    )

    assert [a[A_KEY] for _s, a in conn.inserts] == ["email"]
    assert conn.inserts[0][1][A_VALUE] == "late@acme.co"
    assert conn.inserts[0][1][A_SOURCE] == CAPTURE_SOURCE


@pytest.mark.asyncio
async def test_teardown_with_no_established_facts_writes_nothing():
    from app.services.scripts.call_transcript_persister import (
        save_call_transcript_on_hangup,
    )

    conn, vs, svc = _hangup_fixture(CallState())

    await save_call_transcript_on_hangup(
        voice_session=vs, transcript_service=svc, db_pool=_FakePool(conn)
    )

    assert conn.inserts == []


@pytest.mark.asyncio
async def test_teardown_lead_capture_failure_never_breaks_the_hangup():
    from app.services.scripts.call_transcript_persister import (
        save_call_transcript_on_hangup,
    )

    conn, vs, svc = _hangup_fixture(
        CallState(email="late@acme.co"), fetchrow_exc=RuntimeError("db gone")
    )

    # Must not raise.
    await save_call_transcript_on_hangup(
        voice_session=vs, transcript_service=svc, db_pool=_FakePool(conn)
    )


@pytest.mark.asyncio
async def test_teardown_without_a_dialer_binding_writes_nothing():
    """A browser session that was never bound has no calls row to attach to."""
    from app.services.scripts.call_transcript_persister import (
        save_call_transcript_on_hangup,
    )

    conn, vs, svc = _hangup_fixture(CallState(email="late@acme.co"))
    del vs._dialer_call_id

    await save_call_transcript_on_hangup(
        voice_session=vs, transcript_service=svc, db_pool=_FakePool(conn)
    )

    assert conn.inserts == []


# -- the wiring: a real turn through the real pipeline ------------------------

@pytest.mark.asyncio
async def test_a_completed_turn_writes_the_lead_detail_through_the_pipeline(
    monkeypatch,
):
    """END-TO-END WIRING. Everything above tests the writer in isolation; this
    drives `VoicePipelineService.handle_turn_end` — the actual live-call turn
    path — and asserts a `call_lead_details` INSERT came out of it.

    Without the hook in turn_ender this test fails with zero inserts, which is
    exactly the production state it was written against.
    """
    import asyncio

    from app.domain.models.session import CallSession
    from app.domain.services.voice_pipeline import turn_ender as te
    from app.domain.services.voice_pipeline_service import VoicePipelineService

    conn = _FakeConn()

    monkeypatch.setattr(
        te, "_resolve_transcript_target_call_id", lambda session: CALL
    )
    monkeypatch.setattr(
        te,
        "get_container",
        lambda: SimpleNamespace(is_initialized=True, db_pool=_FakePool(conn)),
    )

    async def _audio(*args, **kwargs):
        yield MagicMock(data=b"\x00" * 320)

    tts = MagicMock()
    tts.stream_synthesize = MagicMock(side_effect=_audio)

    class _LLM:
        async def stream_chat_with_timeout(self, *args, **kwargs):
            yield "Got it, thanks."

    service = VoicePipelineService(
        stt_provider=MagicMock(),
        llm_provider=_LLM(),
        tts_provider=tts,
        media_gateway=AsyncMock(),
        mute_during_tts=False,
    )
    service.latency_tracker = MagicMock()
    service.latency_tracker.get_metrics.return_value = None

    # NOTE tenant_id is deliberately NOT set: voice_orchestrator never sets it
    # and on a campaign with no knowledge base it stays None for the whole
    # call. The binding stamped at answer time is the real source.
    session = CallSession(
        call_id="voice-session-uuid",
        campaign_id=CAMPAIGN,
        lead_id=LEAD,
        provider_call_id="pbx-1",
        system_prompt="Use plain spoken text only.",
        voice_id="voice-1",
    )
    session.barge_in_event = asyncio.Event()
    session._dialer_call_id = CALL
    session._dialer_tenant_id = TENANT
    # The caller already gave their email on an earlier turn; CallState is
    # sticky, so it is still established when this turn completes.
    session.captured_slots = CallState(email="dana@acme.co", email_confirmed=True)
    session.turn_id = 2
    session.current_user_input = "yes that is right, thank you"

    await service.handle_turn_end(session, None)

    assert conn.inserts, (
        "a completed turn on a call that established a fact must write a "
        "call_lead_details row - this is the P0 that was at 0%"
    )
    sql, args = conn.inserts[0]
    assert args[A_KEY] == "email"
    assert args[A_VALUE] == "dana@acme.co"
    assert args[A_SOURCE] == CAPTURE_SOURCE
    assert args[A_TENANT] == TENANT
    assert args[A_CALL] == CALL


@pytest.mark.asyncio
async def test_a_completed_turn_that_established_nothing_writes_nothing(
    monkeypatch,
):
    """The other half of the gate, through the same real path."""
    import asyncio

    from app.domain.models.session import CallSession
    from app.domain.services.voice_pipeline import turn_ender as te
    from app.domain.services.voice_pipeline_service import VoicePipelineService

    conn = _FakeConn()
    monkeypatch.setattr(
        te,
        "get_container",
        lambda: SimpleNamespace(is_initialized=True, db_pool=_FakePool(conn)),
    )

    async def _audio(*args, **kwargs):
        yield MagicMock(data=b"\x00" * 320)

    tts = MagicMock()
    tts.stream_synthesize = MagicMock(side_effect=_audio)

    class _LLM:
        async def stream_chat_with_timeout(self, *args, **kwargs):
            yield "Sure, no problem."

    service = VoicePipelineService(
        stt_provider=MagicMock(),
        llm_provider=_LLM(),
        tts_provider=tts,
        media_gateway=AsyncMock(),
        mute_during_tts=False,
    )
    service.latency_tracker = MagicMock()
    service.latency_tracker.get_metrics.return_value = None

    # NOTE tenant_id is deliberately NOT set: voice_orchestrator never sets it
    # and on a campaign with no knowledge base it stays None for the whole
    # call. The binding stamped at answer time is the real source.
    session = CallSession(
        call_id="voice-session-uuid",
        campaign_id=CAMPAIGN,
        lead_id=LEAD,
        provider_call_id="pbx-1",
        system_prompt="Use plain spoken text only.",
        voice_id="voice-1",
    )
    session.barge_in_event = asyncio.Event()
    session._dialer_call_id = CALL
    session._dialer_tenant_id = TENANT
    session.turn_id = 2
    session.current_user_input = "not really interested in that"

    await service.handle_turn_end(session, None)

    assert conn.inserts == []


# -- the tenant must not come from a field that is constant in prod -----------

def test_the_tenant_is_not_taken_from_the_session_field():
    """THE TRAP. `CallSession.tenant_id` is never set by voice_orchestrator;
    the ONLY writer is knowledge/session_inject, which runs only for campaigns
    that have a knowledge base. On every other campaign it is constant None for
    the whole call, so a capture keyed on it would write nothing in production
    and the P0 would look fixed while staying at 0%.
    """
    session = SimpleNamespace(
        tenant_id=None,
        campaign_id=CAMPAIGN,
        lead_id=LEAD,
        captured_slots=CallState(email="dana@acme.co"),
    )
    session._dialer_call_id = CALL
    session._dialer_tenant_id = TENANT

    binding = resolve_call_binding(session)

    assert binding["tenant_id"] == TENANT
    assert binding["call_id"] == CALL


def test_the_binding_is_read_off_the_telephony_voice_session_for_outbound(
    monkeypatch,
):
    """Outbound stamps the dialer ids on the telephony VoiceSession, not on the
    CallSession — that is where `bind_telephony_call` puts them."""
    from app.domain.services.telephony import lifecycle

    session = SimpleNamespace(
        tenant_id=None,
        campaign_id=None,
        lead_id=None,
        captured_slots=CallState(email="dana@acme.co"),
    )
    vs = SimpleNamespace(call_session=session)
    vs._dialer_call_id = CALL
    vs._dialer_tenant_id = TENANT
    vs._dialer_campaign_id = CAMPAIGN
    vs._dialer_lead_id = LEAD

    monkeypatch.setattr(
        lifecycle,
        "_state",
        lambda: SimpleNamespace(iter_voice_session_items=lambda: [("pbx-1", vs)]),
    )

    binding = resolve_call_binding(session)

    assert binding == {
        "call_id": CALL,
        "tenant_id": TENANT,
        "campaign_id": CAMPAIGN,
        "lead_id": LEAD,
    }


def test_an_unbound_session_resolves_to_no_call(monkeypatch):
    """The other direction: a browser / ask_ai session is in no telephony map,
    so it resolves to nothing and writes nothing."""
    from app.domain.services.telephony import lifecycle

    monkeypatch.setattr(
        lifecycle,
        "_state",
        lambda: SimpleNamespace(iter_voice_session_items=lambda: []),
    )
    session = SimpleNamespace(
        tenant_id=None, campaign_id="ask-ai", lead_id=None,
        captured_slots=CallState(email="dana@acme.co"),
    )

    assert resolve_call_binding(session)["call_id"] is None


@pytest.mark.asyncio
async def test_capture_turn_slots_writes_for_a_bound_call():
    conn = _FakeConn()
    session = SimpleNamespace(
        tenant_id=None, campaign_id=CAMPAIGN, lead_id=LEAD,
        captured_slots=CallState(email="dana@acme.co", email_confirmed=True),
    )
    session._dialer_call_id = CALL
    session._dialer_tenant_id = TENANT

    assert await capture_turn_slots(session, pool=_FakePool(conn)) == 1
    assert conn.inserts[0][1][A_TENANT] == TENANT


@pytest.mark.asyncio
async def test_capture_turn_slots_writes_nothing_for_an_unbound_call(monkeypatch):
    from app.domain.services.telephony import lifecycle

    monkeypatch.setattr(
        lifecycle,
        "_state",
        lambda: SimpleNamespace(iter_voice_session_items=lambda: []),
    )
    conn = _FakeConn()
    session = SimpleNamespace(
        tenant_id=None, campaign_id="ask-ai", lead_id=None,
        captured_slots=CallState(email="dana@acme.co"),
    )

    assert await capture_turn_slots(session, pool=_FakePool(conn)) == 0
    assert conn.statements == []


@pytest.mark.asyncio
async def test_capture_turn_slots_never_raises_when_the_state_map_explodes(
    monkeypatch,
):
    from app.domain.services.telephony import lifecycle

    def _boom():
        raise RuntimeError("state backend down")

    monkeypatch.setattr(lifecycle, "_state", _boom)
    conn = _FakeConn()
    session = SimpleNamespace(
        tenant_id=None, campaign_id=None, lead_id=None,
        captured_slots=CallState(email="dana@acme.co"),
    )

    assert await capture_turn_slots(session, pool=_FakePool(conn)) == 0
