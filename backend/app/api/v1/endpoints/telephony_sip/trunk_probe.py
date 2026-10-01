"""SIP endpoint reachability probe used by POST /trunks/{id}/test.

TCP / TLS: open a real socket to (host, port) — any successful 3-way
            handshake proves the host is at least listening.
UDP:       send a single SIP OPTIONS datagram and wait up to ``timeout``
            for ANY reply (200, 401, 481, etc.). Silence means firewall,
            NAT, or wrong host — caller treats this as "not reachable".

Returns a plain dict (JSON-safe) so the result can be persisted directly
into the tenant_sip_trunks.last_test_result JSONB column without an
intermediate model.
"""
from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import socket
import ssl
import time
import uuid as _uuid
from typing import Any, Dict


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
