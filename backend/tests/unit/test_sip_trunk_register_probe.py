"""REGISTER probe behind POST /trunks/{id}/test (owner: "test ping fetch real status").

The probe proves a line's username and password against the provider without
disturbing the real registration: it sends a REGISTER with a never-registered,
random Contact and ``Expires: 0`` (RFC 3261 10.2.2 / 10.3: this only removes that
one contact binding, which does not exist), answers the provider's digest
challenge exactly once, and reports what the provider said.

A real UDP socket plays the provider, so the datagrams, the digest arithmetic
(checked here with an independent implementation) and the "one attempt, no retry"
rule are all observed on the wire.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
import socket
import threading
from dataclasses import dataclass, field

import pytest

from app.api.v1.endpoints.telephony_sip.trunk_probe import probe_sip_register

LINE_PW = "s3cret-pw-for-tests"
USER = "150001"
DOMAIN = "sip.provider.test"


@dataclass
class Req:
    raw: str
    start: str
    headers: dict = field(default_factory=dict)

    @property
    def method(self):
        return self.start.split(" ", 1)[0]


def parse(datagram: bytes) -> Req:
    raw = datagram.decode("utf-8", errors="replace")
    lines = raw.split("\r\n")
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            key, value = line.split(":", 1)
            headers.setdefault(key.strip().lower(), []).append(value.strip())
    return Req(raw=raw, start=lines[0], headers=headers)


def reply(req: Req, code: int, reason: str, *extra: str, call_id: str | None = None) -> bytes:
    lines = [
        f"SIP/2.0 {code} {reason}",
        *[f"Via: {v}" for v in req.headers["via"]],
        f"From: {req.headers['from'][0]}",
        f"To: {req.headers['to'][0]};tag=srv1",
        f"Call-ID: {call_id or req.headers['call-id'][0]}",
        f"CSeq: {req.headers['cseq'][0]}",
        *extra,
        "Content-Length: 0",
        "", "",
    ]
    return "\r\n".join(lines).encode()


def md5(text: str) -> str:
    return hashlib.md5(text.encode(), usedforsecurity=False).hexdigest()  # RFC 2617 digest, not a secret store


def auth_params(value: str) -> dict:
    assert value.lower().startswith("digest "), value
    return {m.group(1).lower(): (m.group(2) if m.group(2) is not None else m.group(3))
            for m in re.finditer(r'(\w+)\s*=\s*(?:"([^"]*)"|([^\s,]+))', value[7:])}


def digest_ok(req: Req, header: str = "authorization", password: str = LINE_PW) -> bool:
    p = auth_params(req.headers[header][0])
    uri = req.start.split(" ")[1]
    ha1 = md5(f"{p['username']}:{p['realm']}:{password}")
    ha2 = md5(f"REGISTER:{p['uri']}")
    assert p["uri"] == uri, "digest uri must equal the Request-URI"
    if "qop" in p:
        assert p["qop"] == "auth" and p["nc"] == "00000001" and p["cnonce"]
        expected = md5(f"{ha1}:{p['nonce']}:{p['nc']}:{p['cnonce']}:auth:{ha2}")
    else:
        expected = md5(f"{ha1}:{p['nonce']}:{ha2}")
    return p["response"] == expected and p["username"] == USER


CHALLENGE = f'Digest realm="{DOMAIN}", nonce="n0nce123", opaque="op4que", algorithm=MD5, qop="auth"'


class FakeProvider:
    """A UDP SIP 'provider'. ``script(request, index)`` returns datagrams to send back."""

    def __init__(self, script):
        self.script, self.requests = script, []
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.05)
        self.port = self.sock.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def _serve(self):
        while not self._stop.is_set():
            try:
                data, addr = self.sock.recvfrom(8192)
            except socket.timeout:
                continue
            except OSError:
                return
            request = parse(data)
            # Only this probe's REGISTERs count. A stray datagram from another
            # test's socket can land on a reused ephemeral port (CI 2026-10-14
            # counted 2 requests where the probe had sent 1); answering it
            # would also kill this thread on a missing Via header.
            if not request.start.startswith("REGISTER "):
                continue
            self.requests.append(request)
            for datagram in self.script(request, len(self.requests)) or []:
                self.sock.sendto(datagram, addr)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(timeout=2)
        self.sock.close()


@pytest.fixture(autouse=True)
def loopback_allowed(monkeypatch):
    monkeypatch.setenv("TELEPHONY_ALLOW_PRIVATE_SIP_TARGETS", "on")


async def probe(server, **overrides):
    kwargs = dict(host="127.0.0.1", port=server.port, username=USER, password=LINE_PW,
                  domain=DOMAIN, transport="udp", timeout=3.0)
    kwargs.update(overrides)
    return await probe_sip_register(**kwargs)


def challenge_then(final):
    """401 digest challenge, then ``final(req)`` for the authenticated request."""
    def script(req, n):
        if "authorization" not in req.headers:  # stateless, so one server can serve several probes
            return [reply(req, 401, "Unauthorized", f"WWW-Authenticate: {CHALLENGE}")]
        return [final(req)]
    return script


def test_digest_arithmetic_matches_the_rfc_2617_worked_example():
    # RFC 2617 section 3.5: the response value a conforming client must compute.
    from app.api.v1.endpoints.telephony_sip.trunk_probe import _authorization_line

    header = _authorization_line(
        header="Authorization",
        challenge={"realm": "testrealm@host.com", "nonce": "dcd98b7102dd2f0e8b11d0f600bfb0c093",
                   "opaque": "5ccc069c403ebaf9f0171e9517f40e41", "qop": "auth"},
        username="Mufasa", password="Circle Of Life", uri="/dir/index.html", method="GET", cnonce="0a4f113b",
    )
    assert 'response="6629fae49393a05397450978507c4ef1"' in header
    assert 'opaque="5ccc069c403ebaf9f0171e9517f40e41"' in header and "nc=00000001" in header


@pytest.mark.asyncio
async def test_challenge_then_200_means_the_credentials_are_valid():
    with FakeProvider(challenge_then(lambda r: reply(r, 200, "OK") if digest_ok(r) else reply(r, 403, "Forbidden"))) as server:
        result = await probe(server)
    assert result["outcome"] == "accepted" and result["sip_code"] == 200
    assert len(server.requests) == 2, "one challenge-response round, nothing more"
    first, second = server.requests
    assert first.method == second.method == "REGISTER"
    assert "authorization" not in first.headers and "authorization" in second.headers
    assert first.headers["call-id"] == second.headers["call-id"]
    assert (first.headers["cseq"][0], second.headers["cseq"][0]) == ("1 REGISTER", "2 REGISTER")
    assert first.headers["via"][0].split("branch=")[1] != second.headers["via"][0].split("branch=")[1]


@pytest.mark.asyncio
async def test_registration_is_never_disturbed_expires_zero_and_a_unique_contact():
    with FakeProvider(challenge_then(lambda r: reply(r, 200, "OK"))) as server:
        await probe(server)
        await probe(server)
    contacts = []
    for request in server.requests:
        assert request.headers["expires"] == ["0"], "must not create or refresh a binding"
        (contact,) = request.headers["contact"]
        assert contact != "*" and "*" not in contact
        assert re.fullmatch(r"<sip:[^@>]+@[^>]+>;expires=0", contact), contact
        contacts.append(contact)
    # Same contact inside one probe (challenge + response), a new one per probe,
    # and never the trunk's own contact user.
    assert contacts[0] == contacts[1] and contacts[2] == contacts[3]
    assert contacts[0] != contacts[2]
    assert all(f"sip:{USER}@" not in c for c in contacts)
    for request in server.requests:
        assert f"sip:{USER}@{DOMAIN}" in request.headers["from"][0]
        assert f"sip:{USER}@{DOMAIN}" in request.headers["to"][0]
        # Same server_uri / client_uri the generated Asterisk registration uses.
        assert request.start == f"REGISTER sip:{DOMAIN}:{server.port} SIP/2.0"


@pytest.mark.asyncio
async def test_the_password_never_goes_over_the_wire_in_clear():
    with FakeProvider(challenge_then(lambda r: reply(r, 200, "OK"))) as server:
        await probe(server)
    assert all(LINE_PW not in request.raw for request in server.requests)


@pytest.mark.asyncio
async def test_proxy_challenge_is_answered_with_proxy_authorization():
    def script(req, n):
        if n == 1:
            return [reply(req, 407, "Proxy Authentication Required", f"Proxy-Authenticate: {CHALLENGE}")]
        assert "proxy-authorization" in req.headers and "authorization" not in req.headers
        return [reply(req, 200, "OK") if digest_ok(req, "proxy-authorization") else reply(req, 403, "Forbidden")]
    with FakeProvider(script) as server:
        result = await probe(server)
    assert result["outcome"] == "accepted"


@pytest.mark.asyncio
async def test_legacy_challenge_without_qop_is_answered():
    legacy = f'Digest realm="{DOMAIN}", nonce="oldnonce"'

    def script(req, n):
        if n == 1:
            return [reply(req, 401, "Unauthorized", f"WWW-Authenticate: {legacy}")]
        return [reply(req, 200, "OK") if digest_ok(req) else reply(req, 403, "Forbidden")]
    with FakeProvider(script) as server:
        assert (await probe(server))["outcome"] == "accepted"


@pytest.mark.asyncio
async def test_a_second_challenge_is_a_wrong_password_and_is_not_retried():
    with FakeProvider(challenge_then(lambda r: reply(r, 401, "Unauthorized", f"WWW-Authenticate: {CHALLENGE}"))) as server:
        result = await probe(server, password="wrong-pw")
    assert result["outcome"] == "rejected" and result["sip_code"] == 401
    assert len(server.requests) == 2, "providers ban IPs after repeated failures: never retry"


@pytest.mark.asyncio
async def test_a_stale_nonce_is_inconclusive_and_is_not_retried():
    stale = CHALLENGE.replace('nonce="n0nce123"', 'nonce="fresh456", stale=true')
    with FakeProvider(challenge_then(lambda r: reply(r, 401, "Unauthorized", f"WWW-Authenticate: {stale}"))) as server:
        result = await probe(server)
    assert result["outcome"] == "stale" and result["sip_code"] == 401
    assert len(server.requests) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("first_answer,outcome,code,requests", [
    ((403, "Forbidden"), "forbidden", 403, 1),
    ((404, "Not Found"), "not_found", 404, 1),
    ((503, "Service Unavailable"), "server_error", 503, 1),
    ((200, "OK"), "no_challenge", 200, 1),
    ((405, "Method Not Allowed"), "other", 405, 1),
])
async def test_an_answer_to_the_first_request_is_reported_as_is(first_answer, outcome, code, requests):
    with FakeProvider(lambda req, n: [reply(req, *first_answer)]) as server:
        result = await probe(server)
    assert (result["outcome"], result["sip_code"], len(server.requests)) == (outcome, code, requests)


@pytest.mark.asyncio
async def test_a_stray_datagram_from_another_socket_is_not_counted_as_a_request():
    # CI 2026-10-14 counted 2 requests where the probe sent 1: something else
    # reached the fake's reused ephemeral port. Only the probe's REGISTERs count.
    def answer_then_stray(req, n):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as other:
            other.sendto(b"OPTIONS sip:x SIP/2.0\r\n\r\n", ("127.0.0.1", server.port))
            other.sendto(b"\x80\x00 not sip at all", ("127.0.0.1", server.port))
        return [reply(req, 404, "Not Found")]

    with FakeProvider(answer_then_stray) as server:
        result = await probe(server)
        await asyncio.sleep(0.2)  # let the strays arrive before counting
    assert (result["outcome"], result["sip_code"], len(server.requests)) == ("not_found", 404, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("final,outcome", [
    ((403, "Forbidden"), "forbidden"),
    ((404, "Not Found"), "not_found"),
    ((500, "Server Internal Error"), "server_error"),
])
async def test_an_answer_after_the_credentials_were_sent_is_reported_as_is(final, outcome):
    with FakeProvider(challenge_then(lambda r: reply(r, *final))) as server:
        result = await probe(server)
    assert result["outcome"] == outcome and result["sip_code"] == final[0]
    assert len(server.requests) == 2


@pytest.mark.asyncio
async def test_silence_is_a_timeout_after_a_single_datagram():
    with FakeProvider(lambda req, n: None) as server:
        result = await probe(server, timeout=0.3)
    assert result["outcome"] == "timeout" and result["sip_code"] is None
    assert len(server.requests) == 1


@pytest.mark.asyncio
async def test_silence_after_the_credentials_were_sent_is_a_timeout_too():
    def script(req, n):
        return [reply(req, 401, "Unauthorized", f"WWW-Authenticate: {CHALLENGE}")] if n == 1 else None
    with FakeProvider(script) as server:
        result = await probe(server, timeout=1.5)  # long enough for the quick 401, short enough to keep the test fast
    assert result["outcome"] == "timeout" and len(server.requests) == 2


@pytest.mark.asyncio
async def test_provisional_and_spoofed_replies_are_ignored():
    def script(req, n):
        if n == 1:
            return [
                reply(req, 200, "OK", call_id="not-our-call-id"),  # spoof: wrong Call-ID
                reply(req, 100, "Trying"),                          # provisional: keep waiting
                reply(req, 401, "Unauthorized", f"WWW-Authenticate: {CHALLENGE}"),
            ]
        return [reply(req, 100, "Trying"), reply(req, 200, "OK")]
    with FakeProvider(script) as server:
        result = await probe(server)
    assert result["outcome"] == "accepted" and result["sip_code"] == 200


@pytest.mark.asyncio
async def test_an_algorithm_we_cannot_compute_is_inconclusive_and_sends_no_credentials():
    sha = 'Digest realm="x", nonce="n", algorithm=SHA-512-256, qop="auth"'
    with FakeProvider(lambda req, n: [reply(req, 401, "Unauthorized", f"WWW-Authenticate: {sha}")]) as server:
        result = await probe(server)
    assert result["outcome"] == "unsupported" and len(server.requests) == 1


@pytest.mark.asyncio
async def test_a_private_target_is_refused_without_sending_anything(monkeypatch):
    monkeypatch.delenv("TELEPHONY_ALLOW_PRIVATE_SIP_TARGETS")
    with FakeProvider(lambda req, n: [reply(req, 200, "OK")]) as server:
        result = await probe(server)
    assert result["outcome"] == "unsafe_target"
    assert server.requests == []


@pytest.mark.asyncio
async def test_tcp_and_tls_lines_are_not_probed_here():
    with FakeProvider(lambda req, n: [reply(req, 200, "OK")]) as server:
        result = await probe(server, transport="tcp")
    assert result["outcome"] == "unsupported" and server.requests == []


@pytest.mark.asyncio
async def test_an_outbound_proxy_receives_the_datagrams_while_the_uri_names_the_domain():
    with FakeProvider(challenge_then(lambda r: reply(r, 200, "OK"))) as proxy:
        result = await probe_sip_register(
            host="127.0.0.1", port=1, username=USER, password=LINE_PW, domain=DOMAIN,
            proxy=("127.0.0.1", proxy.port), transport="udp", timeout=3.0,
        )
    assert result["outcome"] == "accepted"
    assert all(r.start.startswith(f"REGISTER sip:{DOMAIN}") for r in proxy.requests)
