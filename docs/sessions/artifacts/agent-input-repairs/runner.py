"""Bounded offline pytest runner; no application/network bootstrapping."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import threading

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
phase, *modules = sys.argv[1:]
target = OUT / (phase + ".json")
assert not target.exists()
assert not (ROOT / "backend/.env").exists()
assert os.environ["PYTHONPATH"] == str(ROOT / "backend")
sys.path.insert(0, str(ROOT / "backend"))
original_connect, original_ex, original_pair = socket.socket.connect, socket.socket.connect_ex, socket.socketpair
local = threading.local()
network = {"internal_socketpairs": 0, "prohibited_attempts": 0}
def deny(*args, **kwargs):
    network["prohibited_attempts"] += 1
    raise AssertionError("Provider/database/network calls are not authorized")
def connect(sock, address, original):
    if getattr(local, "pair", False) and address[0] in {"127.0.0.1", "::1"}:
        return original(sock, address)
    return deny()
def pair(*args, **kwargs):
    local.pair = True
    try:
        result = original_pair(*args, **kwargs)
        network["internal_socketpairs"] += 1
        return result
    finally:
        local.pair = False
socket.socket.connect = lambda sock, address: connect(sock, address, original_connect)
socket.socket.connect_ex = lambda sock, address: connect(sock, address, original_ex)
socket.socketpair = pair
async def deny_async(*args, **kwargs):
    return deny()
asyncio.BaseEventLoop.create_connection = deny_async
asyncio.BaseEventLoop.create_datagram_endpoint = deny_async
asyncio.BaseEventLoop.sock_connect = deny_async
if sys.platform == "win32":
    from asyncio.proactor_events import BaseProactorEventLoop
    from asyncio.windows_events import IocpProactor
    BaseProactorEventLoop.sock_connect = deny_async
    IocpProactor.connect = deny
paths = ["backend/app/domain/services/voice_pipeline/turn_ender.py",
         "backend/app/domain/services/voice_pipeline/turn_helpers.py",
         "backend/app/domain/services/end_session_action.py", *modules]
def hashes():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in paths}
before = hashes()
import pytest
argv = [*modules, "-q", "--tb=short", "--disable-warnings", "-o", "addopts="]
code = int(pytest.main(argv))
after = hashes()
target.write_text(json.dumps({"python": sys.executable, "version": sys.version,
    "cwd": str(Path.cwd()), "pytest_argv": argv, "source_before": before,
    "source_after": after, "source_unchanged": before == after,
    "network": network, "pytest_exit_code": code}, indent=2)+"\n", encoding="utf-8")
assert before == after and network["prohibited_attempts"] == 0
raise SystemExit(code)
