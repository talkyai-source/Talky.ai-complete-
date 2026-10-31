"""Run the specified local tests without external socket/async transports."""
import asyncio
import os
from pathlib import Path
import socket
import sys
import threading

ROOT = Path(__file__).resolve().parents[4]
assert not (ROOT / "backend/.env").exists()
sys.path.insert(0, str(ROOT / "backend"))
os.chdir(ROOT / "backend")
original_connect, original_ex, original_pair = socket.socket.connect, socket.socket.connect_ex, socket.socketpair
local = threading.local()
counts = {"internal_socketpairs": 0, "prohibited": 0}
def connect(sock, address, original):
    if getattr(local, "pair", False) and address[0] in {"127.0.0.1", "::1"}:
        return original(sock, address)
    counts["prohibited"] += 1
    raise AssertionError("External sockets are not authorized")
def pair(*args, **kwargs):
    local.pair = True
    try:
        result = original_pair(*args, **kwargs)
        counts["internal_socketpairs"] += 1
        return result
    finally:
        local.pair = False
socket.socket.connect = lambda sock, address: connect(sock, address, original_connect)
socket.socket.connect_ex = lambda sock, address: connect(sock, address, original_ex)
socket.socketpair = pair
async def deny_transport(*args, **kwargs):
    counts["prohibited"] += 1
    raise AssertionError("External transports are not authorized")
asyncio.BaseEventLoop.create_connection = deny_transport
import pytest
code = int(pytest.main(sys.argv[1:] + ["-q", "--tb=short", "--disable-warnings", "-o", "addopts="]))
print("OFFLINE_TRANSPORT_COUNTS", counts)
raise SystemExit(code or int(counts["prohibited"] > 0))
