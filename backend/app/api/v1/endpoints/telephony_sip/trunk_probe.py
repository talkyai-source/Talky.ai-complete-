"""SIP endpoint reachability probe used by POST /trunks/{id}/test.

TCP / TLS: open a real socket to (host, port) — any successful 3-way
            handshake proves the host is at least listening.
UDP:       send a single SIP OPTIONS datagram and wait up to ``timeout``
            for ANY reply (200, 401, 481, etc.). Silence means firewall,
            NAT, or wrong host — caller treats this as "not reachable".

Returns a plain dict (JSON-safe) so the result can be persisted directly
into the tenant_sip_trunks.last_test_result JSONB column without an
intermediate model.

``probe_sip_register`` proves the saved username and password without touching
the line's real registration, and ``run_trunk_test`` strings the checks together
(address, reachability, credentials, live registration) into the step-by-step
answer POST /trunks/{id}/test returns.
"""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import os
import re
import secrets
import socket
import ssl
import time
import uuid as _uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote


def _private_sip_targets_allowed() -> bool:
    return os.getenv("TELEPHONY_ALLOW_PRIVATE_SIP_TARGETS", "").strip().lower() in {
        "1", "true", "on", "yes",
    }


def _address_is_allowed(address: str) -> bool:
    if _private_sip_targets_allowed():
        return True
    try:
        return ipaddress.ip_address(address).is_global
    except ValueError:
        return False


async def resolve_sip_target(
    *, host: str, port: int, socktype: int,
) -> tuple[int, tuple]:
    """Resolve once, reject every non-public answer, and return a pinned peer.

    Connecting to the returned numeric address (rather than resolving the
    tenant hostname a second time) closes DNS-rebinding against the API host.
    Private PBXs require the explicit platform-wide escape hatch
    ``TELEPHONY_ALLOW_PRIVATE_SIP_TARGETS=on`` plus network-level controls.
    """
    try:
        literal_address = ipaddress.ip_address(host)
    except ValueError:
        literal_address = None
    if literal_address is not None and not _address_is_allowed(str(literal_address)):
        raise PermissionError("SIP target resolves to a non-public network address")
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(
        host,
        port,
        family=socket.AF_UNSPEC,
        type=socktype,
    )
    if not infos:
        raise socket.gaierror("Could not resolve SIP target")
    unsafe = sorted({info[4][0] for info in infos if not _address_is_allowed(info[4][0])})
    if unsafe:
        raise PermissionError("SIP target resolves to a non-public network address")
    family, _type, _proto, _canonname, sockaddr = infos[0]
    return family, sockaddr


async def probe_sip_endpoint(
    *, host: str, port: int, transport: str, timeout: float = 5.0,
) -> Dict[str, Any]:
    transport = transport.lower()
    start = time.perf_counter()

    try:
        family, sockaddr = await asyncio.wait_for(
            resolve_sip_target(
                host=host,
                port=port,
                socktype=socket.SOCK_STREAM if transport in ("tcp", "tls") else socket.SOCK_DGRAM,
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        return {
            "ok": False, "latency_ms": int(timeout * 1000), "transport": transport,
            "target": f"{host}:{port}", "error": "timeout", "timeout_code": 408,
            "detail": f"DNS resolution timed out after {timeout}s (local timeout; no SIP status received).",
        }
    except PermissionError as exc:
        return {
            "ok": False,
            "latency_ms": int((time.perf_counter() - start) * 1000),
            "transport": transport,
            "target": f"{host}:{port}",
            "error": "unsafe_target",
            "detail": str(exc),
        }
    except socket.gaierror as exc:
        return {
            "ok": False,
            "latency_ms": int((time.perf_counter() - start) * 1000),
            "transport": transport,
            "target": f"{host}:{port}",
            "error": "dns_failure",
            "detail": str(exc),
        }
    except OSError as exc:
        return {
            "ok": False,
            "latency_ms": int((time.perf_counter() - start) * 1000),
            "transport": transport,
            "target": f"{host}:{port}",
            "error": "network_error",
            "detail": str(exc) or type(exc).__name__,
        }

    if transport in ("tcp", "tls"):
        try:
            if transport == "tls":
                # SIP TLS is commonly self-signed in practice; we want to
                # prove network reachability, not certificate validity.
                ssl_ctx = ssl.create_default_context()
                ssl_ctx.check_hostname = False
                ssl_ctx.verify_mode = ssl.CERT_NONE
                fut = asyncio.open_connection(
                    sockaddr[0],
                    sockaddr[1],
                    family=family,
                    ssl=ssl_ctx,
                    server_hostname=host,
                )
            else:
                fut = asyncio.open_connection(
                    sockaddr[0], sockaddr[1], family=family,
                )
            reader, writer = await asyncio.wait_for(fut, timeout=timeout)
            latency_ms = int((time.perf_counter() - start) * 1000)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            _ = reader
            return {
                "ok": True,
                "latency_ms": latency_ms,
                "transport": transport,
                "target": f"{host}:{port}",
                "detail": "TCP socket accepted",
            }
        except asyncio.TimeoutError:
            return {
                "ok": False,
                "latency_ms": int(timeout * 1000),
                "transport": transport,
                "target": f"{host}:{port}",
                "error": "timeout",
                "timeout_code": 408,
                "detail": f"{transport.upper()} connect timed out after {timeout}s",
            }
        except OSError as exc:
            return {
                "ok": False,
                "latency_ms": int((time.perf_counter() - start) * 1000),
                "transport": transport,
                "target": f"{host}:{port}",
                "error": "connection_refused" if exc.errno in (61, 111) else "network_error",
                "detail": str(exc),
            }
        except Exception as exc:
            return {
                "ok": False,
                "latency_ms": int((time.perf_counter() - start) * 1000),
                "transport": transport,
                "target": f"{host}:{port}",
                "error": "exception",
                "detail": str(exc),
            }

    # UDP path: send a SIP OPTIONS, wait for any reply.
    call_id = _uuid.uuid4().hex
    branch = "z9hG4bK" + _uuid.uuid4().hex[:16]
    tag = _uuid.uuid4().hex[:8]
    options = (
        f"OPTIONS sip:{host}:{port} SIP/2.0\r\n"
        f"Via: SIP/2.0/UDP 0.0.0.0:5060;branch={branch};rport\r\n"
        f"Max-Forwards: 70\r\n"
        f"To: <sip:probe@{host}>\r\n"
        f"From: <sip:probe@talky.ai>;tag={tag}\r\n"
        f"Call-ID: {call_id}\r\n"
        f"CSeq: 1 OPTIONS\r\n"
        f"User-Agent: Talky-Probe/1.0\r\n"
        f"Accept: application/sdp\r\n"
        f"Content-Length: 0\r\n\r\n"
    ).encode()

    # Run the blocking UDP send/recv in a thread executor. The previous
    # loop.sock_sendto/loop.sock_recv approach on a non-blocking socket THROWS
    # under uvloop (which the api service runs), returning error=exception with
    # an empty detail — that was the "Test unreachable despite everything green"
    # bug. A plain blocking socket in a thread is loop-agnostic and reliable.
    def _blocking_probe() -> dict:
        s = socket.socket(family, socket.SOCK_DGRAM)
        try:
            s.settimeout(timeout)
            s.sendto(options, sockaddr)
            try:
                deadline = time.monotonic() + timeout
                while True:
                    s.settimeout(max(0.001, deadline - time.monotonic()))
                    data, peer = s.recvfrom(4096)
                    response = data.decode("ascii", errors="replace")
                    first_line = response.split("\r\n", 1)[0]
                    match = re.fullmatch(r"SIP/2\.0\s+(\d{3})(?:\s+.*)?", first_line)
                    # Ignore unrelated/spoofed traffic: only this OPTIONS reply
                    # from the pinned DNS peer proves SIP reachability.
                    headers = {
                        key.strip().lower(): value.strip()
                        for line in response.split("\r\n")[1:] if ":" in line
                        for key, value in [line.split(":", 1)]
                    }
                    if (
                        peer[:2] == sockaddr[:2]
                        and match
                        and headers.get("call-id", headers.get("i")) == call_id
                        and headers.get("cseq", "").upper() == "1 OPTIONS"
                        and branch in headers.get("via", headers.get("v", ""))
                    ):
                        code = match.group(1)
                        return {
                            "ok": True,
                            "latency_ms": int((time.perf_counter() - start) * 1000),
                            "transport": "udp", "target": f"{host}:{port}",
                            "sip_code": code,
                            "detail": f"SIP server answered {code} to OPTIONS; this does not verify call authorization.",
                        }
                    if time.monotonic() >= deadline:
                        raise socket.timeout()
            except socket.timeout:
                return {
                    "ok": False,
                    "latency_ms": int(timeout * 1000),
                    "transport": "udp", "target": f"{host}:{port}",
                    "error": "timeout", "timeout_code": 408,
                    "detail": f"No SIP response within {timeout}s (local timeout; no SIP status received).",
                }
        except socket.gaierror as exc:
            return {
                "ok": False, "latency_ms": 0, "transport": "udp",
                "target": f"{host}:{port}", "error": "dns_failure", "detail": str(exc),
            }
        except Exception as exc:  # never leave detail empty — surface the real reason
            return {
                "ok": False,
                "latency_ms": int((time.perf_counter() - start) * 1000),
                "transport": "udp",
                "target": f"{host}:{port}",
                "error": "exception",
                "detail": str(exc) or f"{type(exc).__name__}",
            }
        finally:
            try:
                s.close()
            except Exception:
                pass

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _blocking_probe)


# ---------------------------------------------------------------------------
# REGISTER probe: proves the saved username/password, leaves the line alone
# ---------------------------------------------------------------------------
#
# The probe sends REGISTER with a random, never-registered Contact and
# ``Expires: 0`` (and ``;expires=0`` on the Contact). By RFC 3261 10.2.2 and
# 10.3 that can only remove that one contact's binding, which does not exist, so
# the real registration Asterisk holds for the same account is not disturbed,
# while the provider still has to authenticate the request. It never sends
# ``Contact: *``, never registers and then unregisters, answers the digest
# challenge exactly once and never retries: providers ban an IP after a few
# failed sign-ins, and that would take the tenant's live registration down too.
#
# Outcomes (``outcome`` key):
#   accepted      challenged, credentials sent, 2xx            -> credentials valid
#   no_challenge  2xx without any challenge                    -> inconclusive
#   rejected      challenged again after the credentials       -> wrong username/password
#   stale         challenged again with stale=true             -> inconclusive (not retried)
#   forbidden     403           not_found   404
#   server_error  5xx           other       any other final answer
#   timeout       no answer     unsupported TCP/TLS line or an unusable challenge
#   unsafe_target / dns_failure / error     could not send at all

_USER_AGENT = "Talky-Probe/1.0"
_COMPACT_HEADERS = {"i": "call-id", "v": "via", "f": "from", "t": "to", "m": "contact", "l": "content-length"}
_STATUS_LINE = re.compile(r"SIP/2\.0\s+(\d{3})(?:\s+(.*))?")
_AUTH_PARAM = re.compile(r'([A-Za-z][\w-]*)\s*=\s*(?:"([^"]*)"|([^\s,]+))')
_FORBIDDEN_PARTS = re.compile(r'[\x00-\x20\x7f<>"\\;@]')


def _parse_sip_response(data: bytes) -> Optional[Tuple[int, str, Dict[str, List[str]]]]:
    """(status, reason, headers) of a SIP response datagram, else None."""
    text = data.decode("utf-8", errors="replace")
    lines = re.split(r"\r?\n\r?\n", text, maxsplit=1)[0].splitlines()
    if not lines:
        return None
    match = _STATUS_LINE.fullmatch(lines[0].strip())
    if not match:
        return None
    headers: Dict[str, List[str]] = {}
    last: Optional[str] = None
    for line in lines[1:]:
        if line[:1] in (" ", "\t") and last is not None:  # folded header line
            headers[last][-1] += " " + line.strip()
            continue
        if ":" not in line:
            continue
        name, value = line.split(":", 1)
        name = name.strip().lower()
        name = _COMPACT_HEADERS.get(name, name)
        headers.setdefault(name, []).append(value.strip())
        last = name
    return int(match.group(1)), (match.group(2) or "").strip(), headers


def _is_reply_to(headers: Dict[str, List[str]], *, call_id: str, cseq: int, branch: str) -> bool:
    """Only an answer to THIS request counts; spoofed or unrelated traffic is ignored."""
    if (headers.get("call-id") or [""])[0] != call_id:
        return False
    number = (headers.get("cseq") or [""])[0].split()
    if len(number) != 2 or number[0] != str(cseq) or number[1].upper() != "REGISTER":
        return False
    return f"branch={branch}" in (headers.get("via") or [""])[0]


def _digest_challenge(headers: Dict[str, List[str]], header: str) -> Optional[Dict[str, str]]:
    """First Digest challenge this probe can answer (MD5, qop auth or none)."""
    for value in headers.get(header, []):
        if not value.lower().startswith("digest"):
            continue
        params = {
            m.group(1).lower(): (m.group(2) if m.group(2) is not None else m.group(3))
            for m in _AUTH_PARAM.finditer(value[6:])
        }
        if params.get("algorithm", "MD5").upper() != "MD5" or not params.get("realm") or not params.get("nonce"):
            continue
        qops = [q.strip().lower() for q in params.get("qop", "").split(",") if q.strip()]
        if qops and "auth" not in qops:
            continue
        if any('"' in v or "\\" in v for v in params.values()):  # would corrupt the header we echo it into
            continue
        return params
    return None


def _md5(text: str) -> str:
    # RFC 2617 digest arithmetic mandated by the provider; not a password store.
    return hashlib.md5(text.encode("utf-8"), usedforsecurity=False).hexdigest()


def _authorization_line(
    *, header: str, challenge: Dict[str, str], username: str, password: str, uri: str,
    method: str = "REGISTER", cnonce: Optional[str] = None,
) -> str:
    ha1 = _md5(f"{username}:{challenge['realm']}:{password}")
    ha2 = _md5(f"{method}:{uri}")
    quoted_user = username.replace("\\", "\\\\").replace('"', '\\"')
    parts = [f'username="{quoted_user}"', f'realm="{challenge["realm"]}"', f'nonce="{challenge["nonce"]}"', f'uri="{uri}"']
    if challenge.get("qop"):
        nc, cnonce = "00000001", cnonce or secrets.token_hex(8)
        response = _md5(f"{ha1}:{challenge['nonce']}:{nc}:{cnonce}:auth:{ha2}")
        tail = ["algorithm=MD5", "qop=auth", f"nc={nc}", f'cnonce="{cnonce}"']
    else:
        response = _md5(f"{ha1}:{challenge['nonce']}:{ha2}")
        tail = ["algorithm=MD5"]
    parts.append(f'response="{response}"')
    if challenge.get("opaque"):
        parts.append(f'opaque="{challenge["opaque"]}"')
    return f"{header}: Digest " + ", ".join(parts + tail)


def _classify_final(code: int) -> str:
    if code == 403:
        return "forbidden"
    if code == 404:
        return "not_found"
    return "server_error" if code >= 500 else "other"


def _blocking_register(
    *, family: int, sockaddr: tuple, domain: str, port: int, username: str, password: str, timeout: float,
) -> Dict[str, Any]:
    started = time.perf_counter()
    call_id = _uuid.uuid4().hex
    from_tag = _uuid.uuid4().hex[:8]
    contact_user = "probe-" + secrets.token_hex(8)  # random: never a binding that exists
    request_uri = f"sip:{domain}:{port}"
    identity = f"<sip:{quote(username, safe='+-_.~')}@{domain}>"

    def finish(outcome: str, code: Optional[int] = None, reason: str = "", detail: str = "") -> Dict[str, Any]:
        return {
            "outcome": outcome, "sip_code": code, "reason": reason or None,
            "latency_ms": int((time.perf_counter() - started) * 1000), "detail": detail,
        }

    s = socket.socket(family, socket.SOCK_DGRAM)
    try:
        # Connecting a UDP socket sends nothing; it pins the peer (the kernel drops
        # datagrams from anyone else) and reveals the local address for Via/Contact.
        s.connect(sockaddr)
        local_ip, local_port = s.getsockname()[:2]
        local = f"[{local_ip}]:{local_port}" if ":" in local_ip else f"{local_ip}:{local_port}"
        contact = f"<sip:{contact_user}@{local}>;expires=0"

        def send(cseq: int, authorization: Optional[str] = None) -> str:
            branch = "z9hG4bK" + _uuid.uuid4().hex[:16]
            lines = [
                f"REGISTER {request_uri} SIP/2.0",
                f"Via: SIP/2.0/UDP {local};branch={branch};rport",
                "Max-Forwards: 70",
                f"From: {identity};tag={from_tag}",
                f"To: {identity}",
                f"Call-ID: {call_id}",
                f"CSeq: {cseq} REGISTER",
                f"Contact: {contact}",
                "Expires: 0",
                f"User-Agent: {_USER_AGENT}",
            ]
            if authorization:
                lines.append(authorization)
            s.send(("\r\n".join(lines + ["Content-Length: 0", "", ""])).encode("utf-8"))
            return branch

        def final_reply(cseq: int, branch: str):
            deadline = time.monotonic() + timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                s.settimeout(remaining)
                try:
                    parsed = _parse_sip_response(s.recv(65535))
                except socket.timeout:
                    return None
                if parsed and parsed[0] >= 200 and _is_reply_to(parsed[2], call_id=call_id, cseq=cseq, branch=branch):
                    return parsed

        first = final_reply(1, send(1))
        if first is None:
            return finish("timeout", detail=f"No SIP response within {timeout}s (local timeout; no SIP status received).")
        code, reason, headers = first
        if 200 <= code < 300:
            return finish("no_challenge", code, reason, "The provider answered without asking for a password.")
        if code not in (401, 407):
            return finish(_classify_final(code), code, reason, f"SIP {code} {reason}".strip())

        header = "www-authenticate" if code == 401 else "proxy-authenticate"
        challenge = _digest_challenge(headers, header)
        if challenge is None:
            return finish("unsupported", code, reason, "The provider's sign-in challenge is not one this check can answer.")
        authorization = _authorization_line(
            header="Authorization" if code == 401 else "Proxy-Authorization",
            challenge=challenge, username=username, password=password, uri=request_uri,
        )
        second = final_reply(2, send(2, authorization))
        if second is None:
            return finish("timeout", detail=f"No SIP response within {timeout}s to the signed request.")
        code, reason, headers = second
        if 200 <= code < 300:
            return finish("accepted", code, reason, f"SIP {code} {reason}".strip())
        if code in (401, 407):
            retry_header = "www-authenticate" if code == 401 else "proxy-authenticate"
            stale = any(
                re.search(r"stale\s*=\s*\"?true", value, re.I) for value in headers.get(retry_header, [])
            )
            return finish("stale" if stale else "rejected", code, reason, f"SIP {code} {reason}".strip())
        return finish(_classify_final(code), code, reason, f"SIP {code} {reason}".strip())
    except socket.gaierror as exc:
        return finish("dns_failure", detail=str(exc))
    except OSError as exc:  # ICMP unreachable, no route, ...
        return finish("error", detail=str(exc) or type(exc).__name__)
    finally:
        try:
            s.close()
        except Exception:
            pass


async def probe_sip_register(
    *,
    host: str,
    port: int,
    username: str,
    password: str,
    domain: Optional[str] = None,
    transport: str = "udp",
    proxy: Optional[Tuple[str, int]] = None,
    timeout: float = 5.0,
) -> Dict[str, Any]:
    """Prove a line's username/password with one REGISTER round. See the block comment above.

    ``domain`` is the SIP domain used in the URIs (the trunk's ``sip_domain``);
    ``proxy`` is the (host, port) the datagrams are sent to when the trunk uses an
    outbound proxy. The target goes through ``resolve_sip_target`` like every other
    probe, so private and rebinding targets are refused before anything is sent.
    The password is used for the digest only; it is never returned or logged.
    """
    started = time.perf_counter()

    def early(outcome: str, detail: str) -> Dict[str, Any]:
        return {
            "outcome": outcome, "sip_code": None, "reason": None,
            "latency_ms": int((time.perf_counter() - started) * 1000), "detail": detail,
        }

    if (transport or "udp").lower() != "udp":
        return early("unsupported", "Sign-in checks are only run over UDP.")
    domain = (domain or host).strip().lower()
    if not username or not password or _FORBIDDEN_PARTS.search(domain) or re.search(r"[\x00-\x1f\x7f]", username):
        return early("error", "The saved sign-in details cannot be used in a SIP request.")
    target_host, target_port = proxy if proxy else (host, port)
    try:
        family, sockaddr = await asyncio.wait_for(
            resolve_sip_target(host=target_host, port=target_port, socktype=socket.SOCK_DGRAM), timeout=timeout,
        )
    except asyncio.TimeoutError:
        return early("timeout", f"DNS resolution timed out after {timeout}s.")
    except PermissionError as exc:
        return early("unsafe_target", str(exc))
    except socket.gaierror as exc:
        return early("dns_failure", str(exc))
    except OSError as exc:
        return early("error", str(exc) or type(exc).__name__)

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        lambda: _blocking_register(
            family=family, sockaddr=sockaddr, domain=domain, port=port,
            username=username, password=password, timeout=timeout,
        ),
    )


# ---------------------------------------------------------------------------
# The step-by-step test behind POST /trunks/{id}/test
# ---------------------------------------------------------------------------
#
#   address       resolve the host (public-target / SSRF policy)
#   reachability  one OPTIONS (UDP) or a TCP/TLS connect: ANY SIP answer = reachable
#   credentials   one REGISTER round (UDP only); skipped when Asterisk already holds
#                 a fresh registration for the line (that registration IS the proof)
#   registration  what the status updater last saw Asterisk do (database read only)
#
# Network checks stop at the first hard failure; the registration step is only a
# database read, so it is always reported. Our own public address is not a setting
# anywhere in the application, so messages never name it.

_ASK_PROVIDER = "ask your provider to allow connections from our servers"
_FORBIDDEN_TEXT = (
    "Provider refused sign-in. Usually a wrong username or password, our server address isn't allowed, "
    "the account is suspended or out of credit, or too many devices are signed in."
)
_NO_ANSWER_TEXT = f"The server didn't answer. Check the address, or {_ASK_PROVIDER}."
_STATUS_PENDING = {"unregistered", "registering", "checking", "stopped", "failed", "missing_config"}


@dataclass
class _Step:
    key: str
    label: str
    ok: Optional[bool]
    detail: str
    sip_code: Optional[int] = None
    code: str = ""  # internal outcome; never part of the response

    def public(self) -> Dict[str, Any]:
        return {"key": self.key, "ok": self.ok, "label": self.label, "detail": self.detail, "sip_code": self.sip_code}


def _clean_reason(reason: Optional[str]) -> str:
    """A provider-supplied reason phrase, reduced to printable ASCII and capped."""
    return re.sub(r"[^\x20-\x7e]", "", reason or "").strip()[:60]


async def _address_step(host: str, port: int, transport: str, timeout: float) -> _Step:
    socktype = socket.SOCK_STREAM if transport in ("tcp", "tls") else socket.SOCK_DGRAM

    def failed(detail: str, code: str) -> _Step:
        return _Step("address", "Address", False, detail, code=code)

    try:
        _family, sockaddr = await asyncio.wait_for(
            resolve_sip_target(host=host, port=port, socktype=socktype), timeout=timeout,
        )
    except asyncio.TimeoutError:
        return failed(f"Looking up {host} took too long. Try again in a moment.", "timeout")
    except PermissionError:
        return failed(f"{host} points to a private network address, which isn't allowed.", "unsafe_target")
    except socket.gaierror:
        return failed(f"We can't find {host}. Check the spelling.", "dns_failure")
    except OSError:
        return failed(f"Couldn't look up {host}.", "network_error")
    ip = str(sockaddr[0])
    return _Step("address", "Address", True, f"Found {host} ({ip})" if ip != host else f"Using {host}", code="ok")


def _reachability_step(host: str, port: int, probe: Dict[str, Any]) -> _Step:
    label = "Reachability"
    if probe.get("ok"):
        raw = str(probe.get("sip_code") or "")
        sip_code = int(raw) if raw.isdigit() else None
        if sip_code == 403:
            return _Step(
                "reachability", label, True,
                "Reachable, but refusing our server — ask your provider to allow-list our server's IP address.",
                sip_code, "refusing",
            )
        return _Step("reachability", label, True, f"Server reachable ({int(probe.get('latency_ms') or 0)} ms)", sip_code, "ok")
    error = str(probe.get("error") or "error")
    target = f"{host}:{port}"
    if error == "timeout":
        detail = _NO_ANSWER_TEXT
    elif error == "dns_failure":
        detail = f"We can't find {host}. Check the spelling."
    elif error == "unsafe_target":
        detail = f"{host} points to a private network address, which isn't allowed."
    elif error == "connection_refused":
        detail = f"{target} refused the connection. Check the port number and whether the line uses UDP, TCP or TLS."
    else:
        detail = f"Couldn't reach {target}."
    return _Step("reachability", label, False, detail, code=error)


def _registration_step(
    *,
    is_active: bool,
    status: Optional[str],
    detail: Optional[str],
    checked_at: Optional[datetime],
    freshness_seconds: int,
    domain: str,
    now: datetime,
) -> _Step:
    label = "Registration"
    if not is_active:
        return _Step("registration", label, None, "Line is off — turn it on to register.", code="off")
    if checked_at is not None and checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=timezone.utc)
    age = (now - checked_at).total_seconds() if checked_at is not None else None
    if age is None or age > freshness_seconds or age < -5:
        return _Step(
            "registration", label, None,
            "Status unknown — the phone system hasn't reported on this line recently.", code="unknown",
        )
    live = (status or "unknown").strip().lower()
    reason = _clean_reason(detail)
    suffix = f" ({reason})" if reason else ""
    if live == "registered":
        return _Step("registration", label, True, f"Registered with {domain}.", code="registered")
    if live == "reachable":
        return _Step("registration", label, True, "Online — the provider answers the phone system's checks.", code="online")
    if live == "rejected":
        return _Step("registration", label, False, f"The provider rejected the phone system's sign-in{suffix}.", code="rejected")
    if live == "unreachable":
        return _Step("registration", label, False, "The provider isn't answering the phone system's checks.", code="unreachable")
    if live in ("registering", "checking"):
        return _Step("registration", label, None, "Not registered yet — the phone system is still signing in.", code="pending")
    if live in _STATUS_PENDING:
        return _Step(
            "registration", label, False,
            f"Not registered yet — the phone system hasn't been able to sign in{suffix}.", code="pending",
        )
    if live == "loaded":
        return _Step(
            "registration", label, None,
            "Set up and loaded — waiting for the provider to answer the phone system's checks.", code="loaded",
        )
    return _Step("registration", label, None, "Status unknown — the phone system hasn't reported on this line recently.", code="unknown")


def _credentials_from_probe(result: Dict[str, Any], domain: str) -> _Step:
    label = "Credentials"
    outcome = str(result.get("outcome") or "error")
    code = result.get("sip_code")
    reason = _clean_reason(result.get("reason"))

    def step(ok: Optional[bool], detail: str) -> _Step:
        return _Step("credentials", label, ok, detail, code, outcome)

    if outcome == "accepted":
        return step(True, f"Username and password accepted by {domain}.")
    if outcome == "no_challenge":
        return step(None, "The provider accepted us without a password check — it may use IP-based access.")
    if outcome == "rejected":
        return step(False, "Wrong username or password.")
    if outcome == "stale":
        return step(None, "The provider asked us to retry with a fresh sign-in token. Run the test again in a moment.")
    if outcome == "forbidden":
        return step(False, _FORBIDDEN_TEXT)
    if outcome == "not_found":
        return step(False, f"This username doesn't exist on {domain}.")
    if outcome == "server_error":
        return step(False, f"Provider temporarily unavailable ({code}).")
    if outcome == "timeout":
        return step(False, _NO_ANSWER_TEXT)
    if outcome == "unsupported":
        return step(None, "This provider's sign-in method can't be checked from here — the live registration below is the real result.")
    if outcome == "other":
        return step(None, f"The provider answered {code}{' ' + reason if reason else ''} to the sign-in check.")
    return step(None, "The sign-in check could not be run.")


def _proxy_target(metadata: Dict[str, Any], default_port: int = 5060) -> Optional[Tuple[str, int]]:
    """(host, port) of the trunk's outbound proxy, if it has one (already validated at save time)."""
    raw = str(metadata.get("outbound_proxy") or "").strip()
    if not raw:
        return None
    if raw.startswith("["):
        host, _, rest = raw[1:].partition("]")
        port_text = rest[1:] if rest.startswith(":") else ""
    elif raw.count(":") == 1:
        host, port_text = raw.rsplit(":", 1)
    else:
        host, port_text = raw, ""
    try:
        return host, int(port_text) if port_text else default_port
    except ValueError:
        return None


def _overall_status(address: _Step, reach: _Step, creds: _Step, reg: _Step, *, expects_registration: bool) -> str:
    if reg.code in ("registered", "online"):
        return "connected"  # Asterisk's own fresh state is ground truth
    if creds.code in ("rejected", "forbidden", "not_found", "unreadable") or reg.code == "rejected":
        return "credentials_rejected"
    if address.ok is False or reach.ok is False or creds.code in ("timeout", "server_error") or reg.code == "unreachable":
        return "unreachable"
    if reg.code == "pending" or (reg.code == "off" and expects_registration):
        return "not_registered"
    return "reachable"


def _error_code(step: _Step) -> str:
    """Short machine code for the legacy ``error`` field, from the step that failed."""
    if step.key == "credentials":
        if step.code in ("rejected", "forbidden", "not_found"):
            return "credentials_rejected"
        if step.code == "unreadable":
            return "credentials_unreadable"
        return "provider_unavailable" if step.code == "server_error" else step.code
    if step.key == "registration":
        return {"rejected": "credentials_rejected", "pending": "not_registered"}.get(step.code, step.code)
    return step.code


def _verdict(status: str, address: _Step, reach: _Step, creds: _Step, reg: _Step) -> str:
    if status == "connected":
        return (
            "Connected — the line is registered and working."
            if reg.code == "registered" else "Connected — the provider answers and the line is ready."
        )
    if status == "credentials_rejected":
        return creds.detail if creds.code in ("rejected", "forbidden", "not_found", "unreadable") else reg.detail
    if status == "unreachable":
        for step in (address, reach, creds, reg):
            if step.ok is False:
                return step.detail
    if status == "not_registered":
        if reg.code == "off":
            return (
                "Username and password work — turn the line on to register it."
                if creds.code == "accepted" else "Server reachable — turn the line on to register it."
            )
        return reg.detail
    note = ""
    if creds.ok is None and creds.code not in ("none", "skipped", "unsupported"):
        note = " Sign-in could not be confirmed."
    if reg.code == "unknown":
        note += " The phone system hasn't reported on this line recently."
    return f"{reach.detail.rstrip('.')}.{note}"


async def run_trunk_test(
    *,
    host: str,
    port: int,
    transport: str,
    auth_username: Optional[str],
    auth_password: Optional[str],
    metadata: Optional[Dict[str, Any]],
    is_active: bool,
    live_status: Optional[str],
    live_detail: Optional[str],
    live_checked_at: Optional[datetime],
    freshness_seconds: int,
    credentials_problem: Optional[str] = None,
    now: Optional[datetime] = None,
    timeout: float = 5.0,
) -> Dict[str, Any]:
    """Run the four checks for one line and return the JSON-safe result.

    ``credentials_problem`` is a plain-language reason the saved password could not
    be used (for example it could not be decrypted); the credentials step then
    reports it instead of sending anything.
    """
    transport = (transport or "udp").lower()
    meta = metadata if isinstance(metadata, dict) else {}
    expects_registration = bool(meta.get("register"))
    has_credentials = bool(auth_username and (auth_password or credentials_problem))
    registration = _registration_step(
        is_active=bool(is_active), status=live_status, detail=live_detail, checked_at=live_checked_at,
        freshness_seconds=freshness_seconds, domain=host, now=now or datetime.now(timezone.utc),
    )

    # With an outbound proxy, Asterisk sends everything there, so that is what must be
    # found and reachable; the SIP domain still names the account in the URIs.
    proxy = _proxy_target(meta, 5061 if transport == "tls" else 5060)
    net_host, net_port = proxy if proxy else (host, port)
    address = await _address_step(net_host, net_port, transport, timeout)
    probe: Dict[str, Any] = {}
    if address.ok:
        probe = await probe_sip_endpoint(host=net_host, port=net_port, transport=transport, timeout=timeout)
        reach = _reachability_step(net_host, net_port, probe)
    else:
        reach = _Step("reachability", "Reachability", None, "Skipped — the address could not be resolved.", code="skipped")
    network_ok = address.ok is True and reach.ok is True

    label = "Credentials"
    if not has_credentials:
        creds = _Step("credentials", label, None, "No username and password are saved — this line signs in by IP address.", code="none")
    elif credentials_problem:
        creds = _Step("credentials", label, False, credentials_problem, code="unreadable")
    elif registration.code == "registered":
        creds = _Step("credentials", label, True, "Signed in — the line is registered", code="registered")
    elif not network_ok:
        creds = _Step("credentials", label, None, "Skipped — the server could not be reached.", code="skipped")
    elif transport != "udp":
        creds = _Step(
            "credentials", label, None,
            f"Sign-in can't be checked over {transport.upper()} from here — the live registration below is the real result.",
            code="unsupported",
        )
    else:
        creds = _credentials_from_probe(
            await probe_sip_register(
                host=host, port=port, username=str(auth_username), password=str(auth_password),
                domain=host, transport=transport, proxy=proxy, timeout=timeout,
            ),
            host,
        )

    status = _overall_status(address, reach, creds, registration, expects_registration=expects_registration)
    verdict = _verdict(status, address, reach, creds, registration)
    ok = status in ("connected", "reachable") or (status == "not_registered" and not is_active)

    error: Optional[str] = None
    if not ok:
        failed = next((s for s in (address, reach, creds, registration) if s.ok is False), None)
        error = _error_code(failed) if failed is not None else (
            "not_registered" if registration.code == "pending" else None
        )
    return {
        "ok": ok,
        "latency_ms": int(probe.get("latency_ms") or 0),
        "transport": transport,
        "target": f"{net_host}:{net_port}",
        "error": error,
        "detail": verdict,
        "sip_code": probe.get("sip_code"),
        "timeout_code": probe.get("timeout_code"),
        "steps": [step.public() for step in (address, reach, creds, registration)],
        "verdict": verdict,
        "status": status,
    }
