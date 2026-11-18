"""The transfer recording against a real HTTP server on the loopback.

The other transfer-recording tests replace the adapter's HTTP helpers with
fakes. Here nothing is replaced: a small local server answers the four
Asterisk REST calls (start, stop, fetch the stored file, delete) and the
adapter talks to it through its real session, so the request lines, the query
values, the sign-in header and the raw-bytes reader are all exercised.

This is still not a phone system. It proves the adapter sends what the
Asterisk 20 documentation describes and reads back a binary body; it cannot
prove what Asterisk itself does.
"""
import asyncio
import io
import logging
import wave
from types import SimpleNamespace

import aiohttp
import pytest
from aiohttp import web

from app.infrastructure.telephony import asterisk_adapter as aa
from app.infrastructure.telephony.asterisk_adapter import AsteriskAdapter

PARENT = "talky-out-1234abcd"
NAME = aa._transfer_recording_name(PARENT)


def _wav(seconds=1.0, rate=8000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"\x01\x02" * int(rate * seconds))
    return buf.getvalue()


class _PhoneSystem:
    """Answers the four recording calls the way the Asterisk 20 REST docs describe."""

    def __init__(self):
        self.calls = []
        self.not_ready = 0          # how many fetches answer 404 first
        self.record_status = 201
        self.body = _wav()
        self.announce_length = None  # a Content-Length to claim, if set

    def app(self):
        app = web.Application()
        app.router.add_post("/ari/bridges/{bridge}/record", self.record)
        app.router.add_post("/ari/recordings/live/{name}/stop", self.stop)
        app.router.add_get("/ari/recordings/stored/{name}/file", self.file)
        app.router.add_delete("/ari/recordings/stored/{name}", self.delete)
        return app

    def _seen(self, request, kind):
        self.calls.append((kind, request.method, request.path, dict(request.query),
                           request.headers.get("Authorization", "")[:6]))

    async def record(self, request):
        self._seen(request, "record")
        if self.record_status != 201:
            return web.json_response({"message": "refused"}, status=self.record_status)
        return web.json_response({"name": request.query.get("name"), "state": "recording"}, status=201)

    async def stop(self, request):
        self._seen(request, "stop")
        return web.Response(status=204)

    async def file(self, request):
        self._seen(request, "file")
        if self.not_ready > 0:
            self.not_ready -= 1
            return web.json_response({"message": "Recording not found"}, status=404)
        return web.Response(body=self.body, content_type="audio/x-wav")

    async def delete(self, request):
        self._seen(request, "delete")
        return web.Response(status=204)


@pytest.fixture
async def rig():
    phone = _PhoneSystem()
    runner = web.AppRunner(phone.app())
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]

    adapter = AsteriskAdapter()
    adapter._ari_host, adapter._ari_port = "127.0.0.1", port
    adapter._session = aiohttp.ClientSession(auth=aiohttp.BasicAuth("user", "synthetic-only"))
    leg = SimpleNamespace(parent_id=PARENT, bridge_id="bridge-1", durable=False)
    try:
        yield SimpleNamespace(phone=phone, adapter=adapter, leg=leg)
    finally:
        await adapter._session.close()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_start_sends_the_documented_request_and_remembers_the_name(rig):
    await rig.adapter._start_transfer_recording(rig.leg)

    kind, method, path, query, auth = rig.phone.calls[0]
    assert (kind, method, path) == ("record", "POST", "/ari/bridges/bridge-1/record")
    assert query == {
        "name": NAME,
        "format": "wav",
        "ifExists": "overwrite",
        "beep": "false",
        "maxDurationSeconds": "3600",
    }
    assert auth == "Basic ", "the request is signed in like every other call to the phone system"
    assert rig.adapter.transfer_recording_name(PARENT) == NAME


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 404, 409, 422, 500])
async def test_a_refused_start_is_logged_and_nothing_is_remembered(rig, caplog, status):
    rig.phone.record_status = status
    with caplog.at_level(logging.ERROR):
        await rig.adapter._start_transfer_recording(rig.leg)  # must not raise
    assert f"transfer_recording_start_failed parent={PARENT[:12]}" in caplog.text
    assert f"status={status}" in caplog.text
    assert "refused" not in caplog.text, "the answer's body is never logged"
    assert rig.adapter.transfer_recording_name(PARENT) is None


@pytest.mark.asyncio
async def test_the_stored_file_comes_back_as_the_same_bytes(rig):
    data = await rig.adapter.fetch_stored_recording(NAME, attempts=3, delay_s=0.01)
    assert data == rig.phone.body
    with wave.open(io.BytesIO(data), "rb") as wf:
        assert (wf.getnchannels(), wf.getframerate(), wf.getsampwidth()) == (1, 8000, 2)
    assert rig.phone.calls[0][1:3] == ("GET", f"/ari/recordings/stored/{NAME}/file")


@pytest.mark.asyncio
async def test_a_file_that_is_not_there_yet_is_asked_for_again(rig):
    rig.phone.not_ready = 2
    data = await rig.adapter.fetch_stored_recording(NAME, attempts=5, delay_s=0.01)
    assert data == rig.phone.body
    assert [c[0] for c in rig.phone.calls] == ["file", "file", "file"]


@pytest.mark.asyncio
async def test_a_file_that_never_appears_gives_nothing(rig):
    rig.phone.not_ready = 99
    assert await rig.adapter.fetch_stored_recording(NAME, attempts=3, delay_s=0.01) is None
    assert len(rig.phone.calls) == 3


@pytest.mark.asyncio
async def test_a_body_larger_than_the_limit_is_refused(rig):
    with pytest.raises(ValueError):
        await rig.adapter._ari_bytes("GET", f"/recordings/stored/{NAME}/file", max_bytes=100)


@pytest.mark.asyncio
async def test_stop_and_delete_reach_the_documented_addresses(rig):
    await rig.adapter.stop_transfer_recording(NAME)
    await rig.adapter.delete_stored_recording(NAME)
    assert [c[1:3] for c in rig.phone.calls] == [
        ("POST", f"/ari/recordings/live/{NAME}/stop"),
        ("DELETE", f"/ari/recordings/stored/{NAME}"),
    ]


@pytest.mark.asyncio
async def test_the_whole_round_from_start_to_one_joined_recording(rig, monkeypatch):
    """Start, stop, fetch, join onto the agent's part, delete: the real adapter
    and the real join, with only the pending folder and the save replaced."""
    from app.domain.services import recording_spool as spool
    from app.domain.services.telephony import recording as rec
    from app.domain.services.telephony import transfer_recording as tr

    agent_part = io.BytesIO()
    with wave.open(agent_part, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x03\x04\x05\x06" * 32000)  # two seconds
    written, saved = [], []
    monkeypatch.setattr(spool, "read_wav", lambda key: agent_part.getvalue())
    monkeypatch.setattr(spool, "write_entry", lambda entry, wav: written.append(wav))
    monkeypatch.setattr(tr, "purge_old_transfer_parts", lambda **kw: asyncio.sleep(0, result=0))

    async def process(entry, *, wav_bytes=None, voice_session=None):
        saved.append(wav_bytes)
        return "saved"

    monkeypatch.setattr(rec, "process_spool_entry", process)

    await rig.adapter._start_transfer_recording(rig.leg)
    rig.phone.not_ready = 1  # the file appears a moment after the bridge ends
    entry = SimpleNamespace(
        key="k-http", pbx_call_id=PARENT, session_call_id="sess-http", sample_rate=16000,
        duration_seconds=2.0, awaiting="transfer_part", transfer_part=None,
        transfer_recording_name=rig.adapter.transfer_recording_name(PARENT),
        created_at="2026-11-18T00:00:00+00:00", attempts=0, last_error=None, last_attempt_at=None,
    )
    monkeypatch.setattr(rig.adapter, "fetch_stored_recording",
                        lambda name, **kw: AsteriskAdapter.fetch_stored_recording(
                            rig.adapter, name, attempts=4, delay_s=0.01))

    assert await tr.finalize_transfer_entry(entry, adapter=rig.adapter) == "saved"

    assert entry.transfer_part == "included" and entry.awaiting is None
    with wave.open(io.BytesIO(saved[0]), "rb") as wf:
        assert (wf.getnchannels(), wf.getframerate()) == (2, 8000)
        assert abs(wf.getnframes() / 8000 - 3.0) < 0.05, "two seconds of agent part plus one of the transfer"
    assert written == saved
    assert [c[0] for c in rig.phone.calls] == ["record", "stop", "file", "file", "delete"]
    assert rig.adapter.transfer_recording_name(PARENT) is None
