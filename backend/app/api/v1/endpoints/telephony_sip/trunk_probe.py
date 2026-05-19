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
import socket
import ssl
import time
import uuid as _uuid
from typing import Any, Dict


async def probe_sip_endpoint(
    *, host: str, port: int, transport: str, timeout: float = 5.0,
) -> Dict[str, Any]:
    transport = transport.lower()
    start = time.perf_counter()

    if transport in ("tcp", "tls"):
        try:
            if transport == "tls":
                # SIP TLS is commonly self-signed in practice; we want to
                # prove network reachability, not certificate validity.
                ssl_ctx = ssl.create_default_context()
                ssl_ctx.check_hostname = False
                ssl_ctx.verify_mode = ssl.CERT_NONE
                fut = asyncio.open_connection(host, port, ssl=ssl_ctx)
            else:
                fut = asyncio.open_connection(host, port)
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

    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setblocking(False)
        loop = asyncio.get_running_loop()

        addr_info = await loop.run_in_executor(
            None, lambda: socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_DGRAM),
        )
        if not addr_info:
            return {
                "ok": False, "latency_ms": 0, "transport": "udp",
                "target": f"{host}:{port}", "error": "dns_failure",
                "detail": "Could not resolve host",
            }
        sockaddr = addr_info[0][4]

        if hasattr(loop, "sock_sendto"):
            await loop.sock_sendto(sock, options, sockaddr)
        else:
            await loop.run_in_executor(None, lambda: sock.sendto(options, sockaddr))

        try:
            data = await asyncio.wait_for(loop.sock_recv(sock, 4096), timeout=timeout)
            latency_ms = int((time.perf_counter() - start) * 1000)
            first_line = (
                data.split(b"\r\n", 1)[0].decode("ascii", errors="replace") if data else ""
            )
            return {
                "ok": True,
                "latency_ms": latency_ms,
                "transport": "udp",
                "target": f"{host}:{port}",
                "detail": f"Received SIP reply: {first_line[:80]}",
            }
        except asyncio.TimeoutError:
            return {
                "ok": False,
                "latency_ms": int(timeout * 1000),
                "transport": "udp",
                "target": f"{host}:{port}",
                "error": "timeout",
                "detail": "No SIP reply within timeout (possible firewall / NAT / wrong host)",
            }
    except socket.gaierror as exc:
        return {
            "ok": False, "latency_ms": 0, "transport": "udp",
            "target": f"{host}:{port}", "error": "dns_failure", "detail": str(exc),
        }
    except Exception as exc:
        return {
            "ok": False,
            "latency_ms": int((time.perf_counter() - start) * 1000),
            "transport": "udp",
            "target": f"{host}:{port}",
            "error": "exception",
            "detail": str(exc),
        }
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass
