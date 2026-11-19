"""Guards beside the owner's decision of 18 November 2026 ("every recording
recorded at any cost"). The decision opened two defaults for OUTBOUND calls.
These tests pin what it did not change:

* an inbound call is recorded only under a policy the tenant saved;
* a tenant that saved "disabled" is not buffered, not copied to disk and not
  recorded after a transfer;
* a transfer part nobody could join is removed after a week, not kept for ever;
* with no phone system to ask, a transfer part is not given up for a day.
"""
import os
import time
import types
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.domain.services import recording_policy_service as rps
from app.domain.services.telephony import transfer_recording as tr
from app.domain.services.telephony.modes.agent_first import _send_outbound_greeting
from app.domain.services.telephony.modes.caller_first import prepare_inbound_recording
from tests.unit import test_recording_disclosure as base
from tests.unit import test_recording_kept_and_marked as kept
from tests.unit.test_recording_kept_and_marked import fx, save  # noqa: F401 (fixture)

SAVED_ANNOUNCE = base._ANNOUNCE
DISABLED = rps.RecordingDecision(False, False, None, None, 90, "tenant_policy_disabled")


@pytest.fixture(autouse=True)
def _ledger():
    """A clean notice ledger, and the two inbound database reads faked open,
    so the inbound tests below reach the policy decision itself."""
    rps.clear_disclosure_state(base.CALL_UUID)
    with patch(
        "app.domain.services.telephony.modes.caller_first._live_inbound_recording_enabled",
        new=AsyncMock(return_value=True),
    ), patch(
        "app.domain.services.telephony.modes.caller_first._set_inbound_consent_status",
        new=AsyncMock(return_value=None),
    ):
        yield
    rps.clear_disclosure_state(base.CALL_UUID)


# ── inbound keeps its rule ───────────────────────────────────────────────────

def test_only_a_decision_from_a_saved_policy_counts_as_saved():
    assert rps.is_saved_policy(SAVED_ANNOUNCE) is True
    assert rps.is_saved_policy(DISABLED) is True
    assert rps.is_saved_policy(rps._ABSENT_POLICY_DEFAULT) is False
    assert rps.is_saved_policy(rps._POLICY_LOOKUP_FAILURE_DEFAULT) is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "decision", [rps._ABSENT_POLICY_DEFAULT, rps._POLICY_LOOKUP_FAILURE_DEFAULT],
    ids=["nothing_saved", "could_not_be_read"],
)
async def test_inbound_gate_stays_closed_without_a_saved_policy(decision):
    """Both defaults now say "record, with the notice". That was opened for
    outbound calls only: an inbound call still needs a policy the tenant saved."""
    log = []
    voice_session, _ = base._make_voice_session(log)
    gateway = base._GatedInboundMediaGateway(log)
    voice_session.media_gateway = gateway
    p_container, p_policy = base._patched_policy(decision)

    with p_container, p_policy:
        allowed = await prepare_inbound_recording(voice_session)

    assert allowed is False
    assert gateway.enabled is False
    assert ("recording_gate", True) not in log
    assert not any(kind == "tts" for kind, _ in log), "no notice is spoken for a recording that is not made"


@pytest.mark.asyncio
async def test_inbound_gate_still_opens_under_a_saved_policy():
    log = []
    voice_session, _ = base._make_voice_session(log)
    voice_session.media_gateway = base._GatedInboundMediaGateway(log)
    p_container, p_policy = base._patched_policy(SAVED_ANNOUNCE)
    with p_container, p_policy:
        assert await prepare_inbound_recording(voice_session) is True


@pytest.mark.asyncio
async def test_an_inbound_recording_is_refused_at_save_without_a_saved_policy(fx):  # noqa: F811
    fx.decide.return_value = rps._ABSENT_POLICY_DEFAULT
    rps.record_disclosure_state(rps.DISCLOSURE_SPOKEN, kept.CALL)
    outcome = await save(fx, strict_notice=True)
    assert (outcome.status, outcome.reason) == (kept.recording.SAVE_REFUSED, "saved_policy_required")
    assert kept.stored(fx) == 0 and fx.conn.inserts == []


@pytest.mark.asyncio
async def test_an_outbound_recording_is_kept_without_a_saved_policy(fx):  # noqa: F811
    fx.decide.return_value = rps._ABSENT_POLICY_DEFAULT
    rps.record_disclosure_state(rps.DISCLOSURE_SPOKEN, kept.CALL)
    outcome = await save(fx)
    assert outcome.status == kept.recording.SAVE_SAVED
    assert fx.conn.inserts[0][-2] == rps.NOTICE_DELIVERED


# ── a tenant that saved "disabled" ───────────────────────────────────────────

def _outbound_session(log):
    voice_session, session = base._make_voice_session(log)
    voice_session._inbound_admission = None
    voice_session.media_gateway = base._GatedInboundMediaGateway(log)
    return voice_session, session


@pytest.mark.asyncio
async def test_an_outbound_call_of_a_tenant_that_disabled_recording_is_not_buffered():
    """Outbound buffers are open from the start of the media. Once the policy
    says "disabled" they are closed, so nothing of the call is held in memory,
    copied to disk during the call, or recorded after a transfer."""
    log = []
    voice_session, _ = _outbound_session(log)
    p_container, p_policy = base._patched_policy(DISABLED)

    with p_container, p_policy:
        await _send_outbound_greeting(voice_session)

    assert voice_session._recording_refused_by_policy is True
    assert voice_session.media_gateway.enabled is False
    assert ("recording_gate", True) not in log
    assert not any(kind == "tts" for kind, _ in log), "a disabled tenant hears no recording notice"
    assert any(kind == "opener_audio" for kind, _ in log), "the call itself still opens"


@pytest.mark.asyncio
async def test_an_outbound_call_under_a_recording_policy_keeps_its_buffers_open():
    log = []
    voice_session, _ = _outbound_session(log)
    p_container, p_policy = base._patched_policy(SAVED_ANNOUNCE)

    with p_container, p_policy:
        await _send_outbound_greeting(voice_session)

    assert getattr(voice_session, "_recording_refused_by_policy", False) is False
    assert voice_session.media_gateway.enabled is True


def test_the_part_after_a_transfer_is_not_recorded_for_a_tenant_that_disabled_recording(monkeypatch):
    sessions = {
        "pbx-off": SimpleNamespace(_recording_refused_by_policy=True),
        "pbx-on": SimpleNamespace(),
    }
    state = SimpleNamespace(get_voice_session=lambda call_id: sessions.get(call_id))
    from app.domain.services.telephony import lifecycle

    monkeypatch.setattr(lifecycle, "_state", lambda: state)
    assert tr.transfer_recording_allowed("pbx-off") is False
    assert tr.transfer_recording_allowed("pbx-on") is True
    assert tr.transfer_recording_allowed("pbx-unknown") is True, "any doubt records; the save decides again"

    def broken():
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr(lifecycle, "_state", broken)
    assert tr.transfer_recording_allowed("pbx-off") is True


# ── leftovers and "no phone system" ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_transfer_parts_nobody_joined_are_removed_after_a_week(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTERISK_RECORDING_DIR", str(tmp_path))
    monkeypatch.delenv("RECORDING_UNJOINED_TRANSFER_KEEP_DAYS", raising=False)
    old = tmp_path / "talky-xferrec-old.wav"
    fresh = tmp_path / "talky-xferrec-fresh.wav"
    other = tmp_path / "voicemail-greeting.wav"
    for path in (old, fresh, other):
        path.write_bytes(b"RIFF")
    week_and_a_bit = time.time() - 8 * 86400
    os.utime(old, (week_and_a_bit, week_and_a_bit))
    os.utime(other, (week_and_a_bit, week_and_a_bit))

    assert await tr.purge_old_transfer_parts(force=True) == 1
    assert not old.exists(), "an unjoined transfer part older than a week is removed"
    assert fresh.exists(), "a recent one is kept so a person can recover it"
    assert other.exists(), "files that are not ours are never touched"


@pytest.mark.asyncio
async def test_the_clean_up_runs_at_most_once_in_six_hours_and_survives_a_missing_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTERISK_RECORDING_DIR", str(tmp_path / "does-not-exist"))
    assert await tr.purge_old_transfer_parts(force=True) == 0
    monkeypatch.setenv("ASTERISK_RECORDING_DIR", str(tmp_path))
    stale = tmp_path / "talky-xferrec-stale.wav"
    stale.write_bytes(b"RIFF")
    long_ago = time.time() - 30 * 86400
    os.utime(stale, (long_ago, long_ago))
    assert await tr.purge_old_transfer_parts() == 0, "it just ran: not again within six hours"
    assert stale.exists()
    assert await tr.purge_old_transfer_parts(force=True) == 1


class _Spool:
    def __init__(self, monkeypatch):
        from app.domain.services import recording_spool as rs
        from app.domain.services.telephony import recording as rec

        self.updated, self.written, self.processed = [], [], []
        monkeypatch.setattr(rs, "read_wav", lambda key: b"")
        monkeypatch.setattr(rs, "update_entry", self.updated.append)
        monkeypatch.setattr(rs, "write_entry", lambda entry, wav: self.written.append(entry))

        async def process(entry, *, wav_bytes=None, voice_session=None):
            self.processed.append(entry)
            return "saved"

        monkeypatch.setattr(rec, "process_spool_entry", process)
        monkeypatch.setattr(tr, "purge_old_transfer_parts", AsyncMock(return_value=0))


def _entry(age_seconds):
    created = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    return types.SimpleNamespace(
        key="k-guard", pbx_call_id="pbx-guard", session_call_id="sess-guard",
        sample_rate=16000, duration_seconds=2.0, awaiting="transfer_part",
        transfer_recording_name="talky-xferrec-pbx-guard", transfer_part=None,
        created_at=created.isoformat(), attempts=0, last_error=None, last_attempt_at=None,
    )


@pytest.mark.asyncio
async def test_with_no_phone_system_to_ask_the_transfer_part_is_not_given_up_for_a_day(monkeypatch):
    spool = _Spool(monkeypatch)
    entry = _entry(age_seconds=3 * 3600)
    for _ in range(tr._FETCH_TRIES + 2):
        assert await tr.finalize_transfer_entry(entry, adapter=None) == "retry"
    assert entry.awaiting == "transfer_part" and entry.transfer_part is None
    assert entry.attempts == 0, "an unreachable phone system does not use up the tries"
    assert entry.last_error == "transfer_part_no_adapter"
    assert spool.written == [] and spool.processed == []


@pytest.mark.asyncio
async def test_after_a_day_without_the_phone_system_the_agent_part_is_saved_marked_missing(monkeypatch, caplog):
    spool = _Spool(monkeypatch)
    entry = _entry(age_seconds=25 * 3600)
    assert await tr.finalize_transfer_entry(entry, adapter=None) == "saved"
    assert entry.awaiting is None and entry.transfer_part == "missing"
    assert len(spool.processed) == 1


@pytest.mark.asyncio
async def test_the_retry_loop_hook_treats_a_disconnected_phone_system_as_none(monkeypatch):
    seen = []

    async def finalize(entry, *, adapter, voice_session=None):
        seen.append(adapter)
        return "retry"

    monkeypatch.setattr(tr, "finalize_transfer_entry", finalize)
    monkeypatch.setattr(tr, "_live_session_exists", lambda pbx: False)
    with patch(
        "app.domain.services.telephony.adapter_registry.get_adapter",
        return_value=SimpleNamespace(connected=False),
    ):
        await tr.finalize_awaiting_entry(_entry(age_seconds=600))
    assert seen == [None]


# ── transfer on inbound calls (opened 19 November 2026) ──────────────────────

def _registry(monkeypatch, sessions):
    from app.domain.services.telephony import lifecycle

    state = SimpleNamespace(get_voice_session=lambda call_id: sessions.get(call_id))
    monkeypatch.setattr(lifecycle, "_state", lambda: state)


def test_the_part_after_an_inbound_transfer_is_recorded_only_while_that_calls_gate_is_open(monkeypatch):
    """Inbound keeps its stricter rule: the campaign and the platform allow
    recording and the notice was delivered. Any doubt means no."""
    _registry(monkeypatch, {
        "in-open": SimpleNamespace(_recording_allowed=True),
        "in-closed": SimpleNamespace(_recording_allowed=False),
        "in-unset": SimpleNamespace(),
    })
    assert tr.transfer_recording_allowed("in-open", inbound=True) is True
    assert tr.transfer_recording_allowed("in-closed", inbound=True) is False
    assert tr.transfer_recording_allowed("in-unset", inbound=True) is False
    assert tr.transfer_recording_allowed("no-session", inbound=True) is False, (
        "an after-hours transfer has no agent session and no notice: it is not recorded"
    )
    # An open inbound gate does not change the outbound rule, and the reverse.
    assert tr.transfer_recording_allowed("in-closed") is True

    def broken():
        raise RuntimeError("registry unavailable")

    from app.domain.services.telephony import lifecycle

    monkeypatch.setattr(lifecycle, "_state", broken)
    assert tr.transfer_recording_allowed("in-open", inbound=True) is False
    assert tr.transfer_recording_allowed("out", inbound=False) is True


@pytest.mark.asyncio
@pytest.mark.parametrize("gate_open,expected", [(True, 1), (False, 0)])
async def test_the_phone_system_records_an_inbound_handover_only_with_the_gate_open(monkeypatch, gate_open, expected):
    from app.infrastructure.telephony.asterisk_adapter import AsteriskAdapter

    _registry(monkeypatch, {"pbx-in": SimpleNamespace(_recording_allowed=gate_open)})
    adapter = AsteriskAdapter()
    calls = []

    async def fake_ari(method, path, **kwargs):
        calls.append((method, path))
        return {"name": "x", "state": "recording"}

    adapter._ari = fake_ari
    leg = SimpleNamespace(parent_id="pbx-in", bridge_id="bridge-in", durable=True)
    await adapter._start_transfer_recording(leg)
    assert len([c for c in calls if c[1].endswith("/record")]) == expected
    assert (adapter.transfer_recording_name("pbx-in") is not None) is gate_open


@pytest.fixture
def handover(monkeypatch):
    """lifecycle._on_transfer_connected with its collaborators faked."""
    from app.domain.services.telephony import lifecycle

    saved = []

    async def fake_save(vs, call_id, **kw):
        saved.append(kw)

    class _Orchestrator:
        async def end_session(self, vs):
            pass

    holder = SimpleNamespace(vs=None, saved=saved, adapter=None, lifecycle=lifecycle)
    monkeypatch.setattr(lifecycle, "_save_call_recording", fake_save)
    monkeypatch.setattr(lifecycle, "_get_orchestrator", lambda: _Orchestrator())
    monkeypatch.setattr(lifecycle, "_state", lambda: SimpleNamespace(get_voice_session=lambda cid: holder.vs))
    monkeypatch.setattr(lifecycle, "get_adapter", lambda: holder.adapter)
    return holder


def _inbound_session():
    return SimpleNamespace(
        call_id="sess-in", call_session=SimpleNamespace(), config=SimpleNamespace(direction="inbound"),
        _inbound_admission={"call_id": "c"}, _max_call_duration_seconds=1800, pipeline=None,
        transcript_service=None,
    )


@pytest.mark.asyncio
async def test_an_inbound_handover_that_is_being_recorded_waits_for_the_persons_part(handover):
    handover.vs = _inbound_session()
    handover.adapter = SimpleNamespace(transfer_recording_name=lambda call_id: "talky-xferrec-pbx-in")
    await handover.lifecycle._on_transfer_connected("pbx-in", "target-in")
    assert handover.saved == [{"awaiting_transfer": True, "transfer_recording_name": "talky-xferrec-pbx-in"}]
    assert handover.vs._recording_awaiting_transfer_part is True
    assert handover.vs._max_call_duration_seconds == 1800, "the inbound call keeps its own pinned limit"


@pytest.mark.asyncio
async def test_an_inbound_handover_that_is_not_recorded_by_the_phone_system_saves_as_before(handover):
    handover.vs = _inbound_session()
    handover.adapter = SimpleNamespace(transfer_recording_name=lambda call_id: None)
    await handover.lifecycle._on_transfer_connected("pbx-in", "target-in")
    assert handover.saved == [{}], "the agent's part is saved at the handover, with no waiting"
    assert getattr(handover.vs, "_recording_awaiting_transfer_part", False) is False
    assert handover.vs._recording_saved_at_transfer is True


@pytest.mark.asyncio
async def test_an_inbound_persons_part_is_never_kept_without_the_agents_part(handover, monkeypatch, caplog):
    """The agent's part was discarded at the handover because the call's
    recording gate had closed. The person's part goes the same way."""
    from app.domain.services import recording_spool

    monkeypatch.setattr(recording_spool, "list_entries", lambda: [])
    steps = []

    async def stop(name):
        steps.append(("stop", name))

    async def delete(name):
        steps.append(("delete", name))

    handover.adapter = SimpleNamespace(
        transfer_recording_name=lambda call_id: "talky-xferrec-pbx-in",
        stop_transfer_recording=stop, delete_stored_recording=delete,
        forget_transfer_recording=lambda call_id: steps.append(("forget", call_id)),
    )
    written = []
    monkeypatch.setattr(recording_spool, "write_entry", lambda entry, wav: written.append(entry))
    import logging

    with caplog.at_level(logging.INFO):
        await handover.lifecycle._finalize_transfer_recording_at_end(_inbound_session(), "pbx-in")
    assert steps == [("stop", "talky-xferrec-pbx-in"), ("delete", "talky-xferrec-pbx-in"), ("forget", "pbx-in")]
    assert written == [], "nothing is parked for saving"
    assert not handover.lifecycle._TRANSFER_FINALIZE_TASKS
    assert "transfer_recording_discarded call=pbx-in reason=inbound_gate_closed" in caplog.text
