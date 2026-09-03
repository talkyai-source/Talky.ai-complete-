"""Regression tests for two confirmed telephony concurrency races.

Claim 1 (asterisk_adapter): a trunk-created Stasis leg must be paired with the
SPECIFIC origination it belongs to (via CHANNEL(linkedid)), NOT the oldest
pending origination (FIFO). Two calls dialing at once whose legs enter Stasis
out of origination order previously cross-wired — a prospect could get another
tenant's agent/prompt.

Claim 2 (telephony_bridge): early gateway audio must be routed by the FULL
session id / exact mapping. The old truncated ``call_id[:12]`` prefix fallback
let two concurrent calls whose ids collide in the first 12 chars cross audio.
"""

from __future__ import annotations

import base64
import logging
import time

import pytest

# Warm the dependencies <-> tenant_isolation import cycle in the correct order
# BEFORE telephony_bridge is imported lazily below. In a full-suite run an
# earlier test does this; importing it here lets the Claim 2 tests also pass
# when this file is run in isolation. (Pre-existing repo import-order quirk,
# unrelated to the fix.)
import app.api.v1.dependencies  # noqa: F401,E402
from app.infrastructure.metrics import inbound_metrics

from app.infrastructure.telephony.asterisk_adapter import (
    AsteriskAdapter,
    TtsDeliveryError,
)


# ---------------------------------------------------------------------------
# Claim 3 — a dead media leg must hang up the still-live parent
# ---------------------------------------------------------------------------


async def test_on_stasis_end_hangs_up_parent_channel():
    """When teardown runs (e.g. the ExternalMedia leg died while the parent
    PJSIP channel is still Up), the parent must be DELETEd so the caller is not
    left on dead air."""
    ad = AsteriskAdapter()
    parent = "trunk-parent-123"

    deletes = []

    async def fake_ari(
        method,
        path,
        params=None,
        json_body=None,
        ok=(200, 201, 204),
        **kwargs,
    ):
        if method == "DELETE":
            deletes.append(path)
            if kwargs.get("return_status"):
                return 404, {}
        return {}

    async def fake_gateway(method, path, payload=None, ok=(200,)):
        return {}

    ad._ari = fake_ari  # type: ignore[assignment]
    ad._gateway = fake_gateway  # type: ignore[assignment]

    ad._active_sessions[parent] = {"session_id": "sess", "listen_port": 32050, "bridge_id": "br1"}
    ad._ext_channels[parent] = "UnicastRTP/ext-1"
    ad._bridges[parent] = "br1"
    ad._gateway_sessions[parent] = "sess"

    await ad._on_stasis_end(parent, "media_leg_died")

    assert f"/channels/{parent}" in deletes, "parent leg must be hung up on teardown"
    # media leg + bridge also cleaned up.
    assert "/channels/UnicastRTP/ext-1" in deletes
    assert "/bridges/br1" in deletes


# ---------------------------------------------------------------------------
# Claim 4 — TTS delivery failure must surface (not be swallowed as success)
# ---------------------------------------------------------------------------


async def test_send_tts_raises_when_no_gateway_session():
    ad = AsteriskAdapter()
    # No entry in _gateway_sessions for this call.
    with pytest.raises(TtsDeliveryError):
        await ad.send_tts_audio("talky-out-nosession", b"\x7f" * 160)


async def test_send_tts_raises_on_gateway_failure():
    ad = AsteriskAdapter()
    ad._gateway_sessions["call-x"] = "sess-x"

    async def failing_gateway(method, path, payload=None, ok=(200,)):
        raise RuntimeError("gateway 503")

    ad._gateway = failing_gateway  # type: ignore[assignment]

    with pytest.raises(TtsDeliveryError):
        await ad.send_tts_audio("call-x", b"\x7f" * 160)


async def test_send_tts_success_does_not_raise():
    ad = AsteriskAdapter()
    ad._gateway_sessions["call-y"] = "sess-y"

    calls = []

    async def ok_gateway(method, path, payload=None, ok=(200,)):
        calls.append(path)
        return {}

    ad._gateway = ok_gateway  # type: ignore[assignment]

    await ad.send_tts_audio("call-y", b"\x7f" * 160)  # must not raise
    assert calls == ["/v1/sessions/tts/play"]


async def test_first_inbound_agent_audio_is_measured_once_after_gateway_accepts(monkeypatch):
    ad = AsteriskAdapter()
    ad._gateway_sessions["inbound-1"] = "sess-inbound-1"
    ad._active_sessions["inbound-1"] = {
        "direction": "inbound",
        "answered_at_monotonic": time.monotonic() - 0.25,
        "first_agent_audio_recorded": False,
    }
    observed: list[float] = []

    async def ok_gateway(method, path, payload=None, ok=(200,)):
        return {}

    monkeypatch.setattr(ad, "_gateway", ok_gateway)
    monkeypatch.setattr(
        inbound_metrics,
        "record_inbound_answer_to_first_audio",
        observed.append,
    )

    await ad.send_tts_audio("inbound-1", b"\x7f" * 160)
    await ad.send_tts_audio("inbound-1", b"\x7f" * 160)

    assert len(observed) == 1
    assert observed[0] >= 0.25
    assert ad._active_sessions["inbound-1"]["first_agent_audio_recorded"] is True


# ---------------------------------------------------------------------------
# Claim 1 — deterministic trunk-leg → origination correlation
# ---------------------------------------------------------------------------


def _adapter_with_linkedids(linkedid_by_channel):
    """Build an adapter whose _ari GET CHANNEL(linkedid) returns a canned value
    per trunk-leg channel id (None → simulate a read that yields no value)."""
    ad = AsteriskAdapter()

    async def fake_ari(method, path, params=None, json_body=None, ok=(200, 201, 204)):
        # path is /channels/{id}/variable ; extract the channel id.
        assert method == "GET"
        cid = path.split("/channels/", 1)[1].split("/variable", 1)[0]
        val = linkedid_by_channel.get(cid)
        return {"value": val or ""}

    ad._ari = fake_ari  # type: ignore[assignment]
    return ad


async def test_out_of_order_trunk_legs_map_to_correct_parents():
    """Two originations pending (A then B). B's trunk leg enters Stasis FIRST.
    FIFO would hand B's leg the older origination A — the cross-wire. With
    linkedid correlation each leg claims its OWN origination."""
    A_pre = "talky-out-aaaaaaaa-1111-2222-3333-444444444444"
    B_pre = "talky-out-bbbbbbbb-5555-6666-7777-888888888888"

    ad = _adapter_with_linkedids(
        {
            "trunk-legB": B_pre,  # B's leg reports linkedid == B's origination id
            "trunk-legA": A_pre,
        }
    )
    # Registered in origination order A, then B.
    ad._track_originated_channel(A_pre)
    ad._track_originated_channel(B_pre)

    # B answers first (out of origination order).
    matched_b = await ad._correlate_trunk_leg("trunk-legB")
    assert matched_b == B_pre, "B's trunk leg must map to B, not the oldest (A)"

    # Then A.
    matched_a = await ad._correlate_trunk_leg("trunk-legA")
    assert matched_a == A_pre

    # Both originations consumed exactly once; nothing left dangling.
    assert ad._originated_channels == set()
    assert ad._originated_channel_order == []


async def test_linkedid_match_does_not_depend_on_arrival_order():
    """Same two calls, legs arrive in origination order — still correct."""
    A_pre = "talky-out-aaaa1111"
    B_pre = "talky-out-bbbb2222"
    ad = _adapter_with_linkedids({"legA": A_pre, "legB": B_pre})
    ad._track_originated_channel(A_pre)
    ad._track_originated_channel(B_pre)

    assert await ad._correlate_trunk_leg("legA") == A_pre
    assert await ad._correlate_trunk_leg("legB") == B_pre
    assert ad._originated_channels == set()


async def test_no_linkedid_fails_closed_instead_of_guessing(caplog):
    """No linkedid (old Asterisk / id not honoured) → REFUSE to correlate.

    The FIFO fallback this test used to assert was removed deliberately: a
    genuine inbound PJSIP leg can enter Stasis while outbound originations are
    pending, and handing it the oldest pending id attaches one caller to
    another tenant's outbound call. Fail closed instead — return None, leave
    every pending origination intact, and say so loudly in the log.
    """
    A_pre = "talky-out-aaaa1111"
    B_pre = "talky-out-bbbb2222"
    ad = _adapter_with_linkedids({})  # every read returns empty
    ad._track_originated_channel(A_pre)
    ad._track_originated_channel(B_pre)

    with caplog.at_level(logging.ERROR):
        assert await ad._correlate_trunk_leg("legX") is None
        assert await ad._correlate_trunk_leg("legY") is None

    # Nothing was consumed — both originations are still pending for their own
    # legs, and neither caller was cross-wired.
    assert ad._originated_channels == {A_pre, B_pre}
    assert "refusing to guess" in caplog.text


async def test_linkedid_read_error_fails_closed(caplog):
    """An ARI failure must not degrade into a guess either."""
    A_pre = "talky-out-aaaa1111"
    ad = AsteriskAdapter()

    async def boom(method, path, params=None, json_body=None, ok=(200, 201, 204)):
        raise RuntimeError("ARI down")

    ad._ari = boom  # type: ignore[assignment]
    ad._track_originated_channel(A_pre)

    with caplog.at_level(logging.ERROR):
        assert await ad._correlate_trunk_leg("leg") is None  # no crash, no guess
    assert ad._originated_channels == {A_pre}
    assert "refusing to guess" in caplog.text


async def test_no_pending_originations_returns_none():
    ad = _adapter_with_linkedids({"leg": "some-unrelated-linkedid"})
    # linkedid doesn't match any pending (there are none) → nothing to consume.
    assert await ad._correlate_trunk_leg("leg") is None


async def test_start_trunk_leg_aliases_correct_pair():
    """End-to-end: _start_trunk_leg must alias the ACTUAL leg id to the matched
    origination id (not the oldest) and then run outbound setup."""
    A_pre = "talky-out-aaaaaaaa"
    B_pre = "talky-out-bbbbbbbb"
    ad = _adapter_with_linkedids({"trunk-legB": B_pre})
    ad._track_originated_channel(A_pre)
    ad._track_originated_channel(B_pre)

    aliases = []
    ad.set_outbound_channel_alias_callback(lambda orig, actual: aliases.append((orig, actual)))

    started = []

    async def fake_start(cid):
        started.append(cid)

    ad._on_outbound_stasis_start = fake_start  # type: ignore[assignment]

    await ad._start_trunk_leg("trunk-legB")

    assert aliases == [(B_pre, "trunk-legB")], "must alias B's origination, not A's"
    assert started == ["trunk-legB"]
    assert A_pre in ad._originated_channels  # A untouched, still ringing


async def test_start_trunk_leg_refuses_media_when_alias_persistence_fails():
    """The actual PBX id must be durable before media setup can own the call."""
    pre_id = "talky-out-aaaaaaaa"
    actual_id = "trunk-leg-a"
    ad = _adapter_with_linkedids({actual_id: pre_id})
    ad._track_originated_channel(pre_id)

    async def failed_alias(_original, _actual):
        raise RuntimeError("database unavailable")

    ad.set_outbound_channel_alias_callback(failed_alias)
    started = []
    scheduled_hangups = []

    async def fake_start(channel_id):
        started.append(channel_id)

    ad._on_outbound_stasis_start = fake_start  # type: ignore[assignment]
    ad._schedule_unclaimed_hangup = (  # type: ignore[assignment]
        lambda channel_id, *, reason: scheduled_hangups.append((channel_id, reason))
    )

    await ad._start_trunk_leg(actual_id)

    assert started == []
    assert scheduled_hangups == [(actual_id, "outbound_alias_persist_failed")]


# ---------------------------------------------------------------------------
# Claim 2 — early audio must never bind to the wrong session via [:12] prefix
# ---------------------------------------------------------------------------


class _FakeStateBackend:
    def __init__(self, direct_map):
        self._direct = dict(direct_map)  # session_id -> call_id (exact)
        self.early = {}  # session_id -> list[bytes]

    def get_call_id_for_gateway_session(self, session_id):
        return self._direct.get(session_id)

    def append_early_audio(self, session_id, audio):
        buf = self.early.setdefault(session_id, [])
        buf.append(audio)
        return len(buf)


class _FakeRequest:
    def __init__(self, body, *, internal_token: str):
        self._body = body
        self.headers = {"x-internal-service-token": internal_token}

    async def json(self):
        return self._body


async def test_colliding_prefix_ids_do_not_cross_audio(monkeypatch):
    """Two concurrent outbound calls whose ids share the first 12 chars
    (``talky-out-3f``). Audio for call B arrives before B is registered; it must
    NOT be routed into call A — it must be buffered under B's exact session id."""
    import app.api.v1.endpoints.telephony_bridge as tb

    call_a = "talky-out-3f000000-aaaa-A"
    call_b = "talky-out-3f000000-bbbb-B"
    # Precondition: the two ids genuinely collide on the truncated prefix the
    # old fallback used, so this test would FAIL against the old cross-wiring.
    assert call_a[:12] == call_b[:12] == "talky-out-3f"

    session_a = f"asterisk-{call_a[:12]}-32001"
    session_b = f"asterisk-{call_b[:12]}-32002"

    # Only call A is registered (exact mapping). B has not registered yet.
    fake_sb = _FakeStateBackend({session_a: call_a})

    routed = []

    async def fake_on_audio(call_id, audio):
        routed.append((call_id, audio))

    monkeypatch.setattr(tb, "get_state_backend", lambda: fake_sb)
    monkeypatch.setattr(tb, "_on_audio_received", fake_on_audio)
    internal_token = "concurrency-race-test-token"
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", internal_token)

    audio_bytes = b"\x7f" * 160
    body = {"session_id": session_b, "pcmu_base64": base64.b64encode(audio_bytes).decode()}

    resp = await tb.receive_gateway_audio(
        session_b, _FakeRequest(body, internal_token=internal_token)
    )

    # No cross-route into call A.
    assert routed == [], "early audio for B must not enter call A's pipeline"
    # Buffered under B's EXACT session id for later drain.
    assert fake_sb.early.get(session_b) == [audio_bytes]
    assert session_a not in fake_sb.early
    assert resp.status_code == 200


async def test_exact_session_audio_routes_normally(monkeypatch):
    """Sanity: once the exact mapping exists, audio routes to that call."""
    import app.api.v1.endpoints.telephony_bridge as tb

    call_a = "talky-out-3f000000-aaaa-A"
    session_a = f"asterisk-{call_a[:12]}-32001"
    fake_sb = _FakeStateBackend({session_a: call_a})

    routed = []

    async def fake_on_audio(call_id, audio):
        routed.append((call_id, audio))

    monkeypatch.setattr(tb, "get_state_backend", lambda: fake_sb)
    monkeypatch.setattr(tb, "_on_audio_received", fake_on_audio)
    internal_token = "concurrency-race-test-token"
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", internal_token)

    audio_bytes = b"\x01" * 160
    body = {"session_id": session_a, "pcmu_base64": base64.b64encode(audio_bytes).decode()}
    await tb.receive_gateway_audio(session_a, _FakeRequest(body, internal_token=internal_token))

    assert routed == [(call_a, audio_bytes)]
    assert fake_sb.early == {}
