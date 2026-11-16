"""Live calls must drain BEFORE the stop signal, while the listener is open.

uvicorn closes its listening socket before the app's shutdown hook runs, so
the in-app drain in ``main.py`` cannot work (the gateway's audio POSTs are
refused and the agent goes deaf). The fix is an ``ExecStop`` step that asks
the running process to drain (``POST /api/v1/sip/telephony/drain``) and then
polls ``/api/v1/healthz/ready`` until no call is committed.
"""
from __future__ import annotations

import importlib.util
import json
import re
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from app.core import readiness

BACKEND = Path(__file__).resolve().parents[2]
SCRIPT = BACKEND / "scripts" / "drain_calls.py"
UNIT = BACKEND / "systemd" / "talky-api.service"
TOKEN = "synthetic-drain-token-not-a-real-secret"


@pytest.fixture(autouse=True)
def _reset_readiness():
    def reset():
        readiness._draining = False
        readiness._drain_started_at = 0.0
        readiness._active_count_provider = None
        readiness._max_capacity_provider = None
        readiness._committed_count_provider = None

    reset()
    yield
    reset()


# ── readiness ────────────────────────────────────────────────────────────


def test_second_begin_drain_keeps_first_start_time(monkeypatch):
    clock = iter([100.0, 200.0])
    monkeypatch.setattr(readiness.time, "monotonic", lambda: next(clock))
    readiness.begin_drain()
    readiness.begin_drain()
    assert readiness._drain_started_at == 100.0


def test_snapshot_reports_committed_calls_from_third_provider():
    readiness.set_capacity_providers(lambda: 2, lambda: 10, lambda: 5)
    snap = readiness.snapshot()
    assert snap["committed_calls"] == 5
    assert snap["active_sessions"] == 2
    assert readiness.committed_calls() == 5


def test_committed_calls_defaults_to_active_count():
    readiness.set_capacity_providers(lambda: 3, lambda: 10)
    assert readiness.committed_calls() == 3
    assert readiness.snapshot()["committed_calls"] == 3


def test_committed_calls_zero_without_providers():
    assert readiness.committed_calls() == 0


def test_raising_providers_never_raise():
    def boom():
        raise RuntimeError("provider down")

    readiness.set_capacity_providers(lambda: 4, lambda: 10, boom)
    assert readiness.committed_calls() == 4
    readiness.set_capacity_providers(boom, lambda: 10, boom)
    assert readiness.committed_calls() == 0
    readiness.set_capacity_providers(lambda: 4, lambda: 10, boom)
    assert readiness.snapshot()["committed_calls"] == 4


# ── route ────────────────────────────────────────────────────────────────


def _client():
    from app.api.v1.endpoints.telephony_drain import router

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def test_drain_route_refuses_without_credentials(monkeypatch):
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", TOKEN)
    async with _client() as c:
        r = await c.post("/api/v1/sip/telephony/drain")
    assert r.status_code == 401
    assert readiness.is_draining() is False


async def test_drain_route_with_internal_token_starts_drain(monkeypatch):
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", TOKEN)
    monkeypatch.setenv("TELEPHONY_ENABLED", "true")
    readiness.set_capacity_providers(lambda: 2, lambda: 10, lambda: 2)
    async with _client() as c:
        r = await c.post(
            "/api/v1/sip/telephony/drain",
            headers={"X-Internal-Service-Token": TOKEN},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["draining"] is True
    assert body["committed_calls"] == 2
    assert readiness.is_draining() is True


async def test_drain_route_refused_when_telephony_off(monkeypatch):
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", TOKEN)
    monkeypatch.setenv("TELEPHONY_ENABLED", "false")
    async with _client() as c:
        r = await c.post(
            "/api/v1/sip/telephony/drain",
            headers={"X-Internal-Service-Token": TOKEN},
        )
    assert r.status_code == 503
    assert readiness.is_draining() is False


# ── script ───────────────────────────────────────────────────────────────


def _load_script():
    spec = importlib.util.spec_from_file_location("drain_calls_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Server:
    """Local stand-in for talky-api: drain route + ready route."""

    def __init__(self, drain_status=200, counts=(2, 1, 0)):
        outer = self
        self.drain_status = drain_status
        self.counts = list(counts)
        self.ready_polls = 0
        self.drain_posts = 0
        self.drain_token_header = None

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, status, payload):
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                if self.path == "/api/v1/sip/telephony/drain":
                    outer.drain_posts += 1
                    outer.drain_token_header = self.headers.get("X-Internal-Service-Token")
                    self._send(outer.drain_status, {"draining": True})
                else:
                    self._send(404, {})

            def do_GET(self):
                if self.path == "/api/v1/healthz/ready":
                    i = min(outer.ready_polls, len(outer.counts) - 1)
                    outer.ready_polls += 1
                    self._send(503, {"ready": False, "committed_calls": outer.counts[i]})
                else:
                    self._send(404, {})

        self.httpd = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *a):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def script_env(monkeypatch):
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", TOKEN)
    monkeypatch.setenv("DRAIN_TIMEOUT_S", "30")
    return monkeypatch


def test_script_waits_until_committed_calls_reach_zero(script_env, capsys):
    with _Server(counts=(2, 1, 0)) as srv:
        script_env.setenv("TALKY_CALL_API_URL", srv.url)
        rc = _load_script().main([], poll_interval_s=0.01)
    assert rc == 0
    assert srv.drain_posts == 1
    assert srv.drain_token_header == TOKEN
    assert srv.ready_polls == 3


def test_script_proceeds_when_drain_route_missing(script_env):
    with _Server(drain_status=404) as srv:
        script_env.setenv("TALKY_CALL_API_URL", srv.url)
        rc = _load_script().main([], poll_interval_s=0.01)
    assert rc == 0
    assert srv.ready_polls == 0


def test_script_returns_quickly_when_nothing_listens(script_env):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    script_env.setenv("TALKY_CALL_API_URL", f"http://127.0.0.1:{port}")
    start = time.monotonic()
    rc = _load_script().main([], poll_interval_s=0.01)
    assert rc == 0
    assert time.monotonic() - start < 5


def test_script_gives_up_at_deadline(script_env):
    with _Server(counts=(3,)) as srv:
        script_env.setenv("TALKY_CALL_API_URL", srv.url)
        script_env.setenv("DRAIN_TIMEOUT_S", "0.3")
        start = time.monotonic()
        rc = _load_script().main([], poll_interval_s=0.05)
        elapsed = time.monotonic() - start
    assert rc == 0
    assert srv.ready_polls >= 2
    assert 0.25 <= elapsed < 5


def test_script_never_prints_the_token(script_env, capsys):
    for status in (200, 401, 500):
        with _Server(drain_status=status, counts=(1, 0)) as srv:
            script_env.setenv("TALKY_CALL_API_URL", srv.url)
            _load_script().main([], poll_interval_s=0.01)
    script_env.delenv("TALKY_CALL_API_URL")
    _load_script().main([], poll_interval_s=0.01)
    out = capsys.readouterr()
    assert TOKEN not in out.out
    assert TOKEN not in out.err


def test_script_without_token_proceeds(monkeypatch):
    monkeypatch.delenv("INTERNAL_SERVICE_TOKEN", raising=False)
    with _Server() as srv:
        monkeypatch.setenv("TALKY_CALL_API_URL", srv.url)
        rc = _load_script().main([], poll_interval_s=0.01)
    assert rc == 0
    assert srv.drain_posts == 0


# ── systemd unit ─────────────────────────────────────────────────────────


def test_unit_drains_before_stop_signal_and_waits_long_enough():
    text = UNIT.read_text(encoding="utf-8")
    m = re.search(r"^ExecStop=.*scripts/drain_calls\.py\s*$", text, re.M)
    assert m, "ExecStop must run scripts/drain_calls.py"
    assert SCRIPT.is_file()
    t = re.search(r"^TimeoutStopSec=(\d+)\s*$", text, re.M)
    assert t, "TimeoutStopSec must be set"
    assert int(t.group(1)) > 300
