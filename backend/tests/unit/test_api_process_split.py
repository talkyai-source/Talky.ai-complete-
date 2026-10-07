"""Core-infra step 4 (2026-10-07): the dashboard API and the live-call API run
as separate processes, and nginx sends every live-call route to the call one.

Per-call state lives in the call process's memory (telephony_bridge module
dicts, AsteriskAdapter, VoiceOrchestrator), so a live-call route that reached
the dashboard process would find no call: hangups would 503 and campaign
pause/stop would leave calls up until the watchdog retried.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
NGINX = BACKEND / "deploy" / "infra" / "nginx" / "api.talkleeai.com.conf"


# ── the switch ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("value", [None, "true", "1", "yes", "TRUE"])
def test_telephony_stays_on_unless_explicitly_disabled(monkeypatch, value):
    from app.core.process_role import telephony_enabled

    if value is None:
        monkeypatch.delenv("TELEPHONY_ENABLED", raising=False)
    else:
        monkeypatch.setenv("TELEPHONY_ENABLED", value)
    assert telephony_enabled() is True


@pytest.mark.parametrize("value", ["false", "0", "no", "off", " False "])
def test_telephony_can_be_switched_off(monkeypatch, value):
    from app.core.process_role import telephony_enabled

    monkeypatch.setenv("TELEPHONY_ENABLED", value)
    assert telephony_enabled() is False


def test_main_claims_ownership_and_connects_only_when_telephony_is_enabled():
    src = (BACKEND / "app" / "main.py").read_text(encoding="utf-8")
    gate = src.index("    if _telephony_enabled:\n")
    assert gate < src.index("acquire_telephony_ownership_strict()")
    assert gate < src.index("await _state_backend.start_heartbeat()")
    # the owner / retry branches are both behind the disabled branch
    disabled = src.index("    if not _telephony_enabled:\n")
    assert disabled < src.index("    elif not _is_owner:\n") < src.index("await _auto_connect_telephony()")
    assert "telephony_disabled_on_this_process" in src


# ── the two units ───────────────────────────────────────────────────────


def _unit(name: str) -> str:
    return (BACKEND / "systemd" / name).read_text(encoding="utf-8")


def test_dashboard_unit_has_telephony_off_on_a_private_port():
    web = _unit("talky-api-web.service")
    assert "Environment=TELEPHONY_ENABLED=false" in web
    assert "--host 127.0.0.1 --port 8001" in web
    assert "--workers 1" in web  # slowapi limiter counters are per process
    assert "WantedBy=multi-user.target" in web


def test_call_unit_keeps_telephony_and_the_port_the_gateway_calls():
    call = _unit("talky-api.service")
    assert "TELEPHONY_ENABLED=false" not in call
    assert "--port 8000" in call


def test_installer_enables_the_dashboard_unit():
    installer = _unit("install-services.sh")
    assert "systemctl enable talky-api-web.service" in installer


# ── nginx routing ───────────────────────────────────────────────────────


def _routes():
    text = NGINX.read_text(encoding="utf-8")
    block = text[text.index('map "$request_method:$uri" $talky_upstream {'):]
    block = block[: block.index("}")]
    default = re.search(r"^\s*default\s+(\w+);", block, re.M).group(1)
    rules = [(re.compile(m.group(1)), m.group(2))
             for m in re.finditer(r'^\s*"~([^"]+)"\s+(\w+);', block, re.M)]
    return default, rules


def _route(method: str, uri: str) -> str:
    default, rules = _routes()
    key = f"{method}:{uri}"
    for rx, upstream in rules:
        if rx.search(key):
            return upstream
    return default


CALL = [
    ("GET", "/api/v1/healthz/ready"),                 # call capacity/drain, not dashboard state
    ("GET", "/api/v1/healthz/deep"),                  # call-process dependency probe
    ("POST", "/api/v1/sip/telephony/audio/7f3a"),        # gateway audio (if ever proxied)
    ("POST", "/api/v1/sip/telephony/call"),              # dialer origination via API_BASE_URL
    ("POST", "/api/v1/sip/telephony/hangup/abc"),
    ("GET", "/api/v1/sip/telephony/status"),
    ("POST", "/api/v1/twilio/answer"),
    ("GET", "/api/v1/twilio/media-stream"),
    ("GET", "/api/v1/vonage/ws-audio/uuid-1"),
    ("POST", "/api/v1/calls/1234/hangup"),
    ("POST", "/api/v1/admin/calls/1234/terminate"),
    ("POST", "/api/v1/campaigns/c1/pause"),
    ("POST", "/api/v1/campaigns/c1/stop"),
    ("DELETE", "/api/v1/campaigns/c1"),
]
WEB = [
    ("GET", "/health"),
    ("GET", "/api/v1/campaigns/"),
    ("GET", "/api/v1/campaigns/c1"),
    ("PUT", "/api/v1/campaigns/c1"),
    ("POST", "/api/v1/campaigns/c1/start"),
    ("GET", "/api/v1/calls/1234"),
    ("GET", "/api/v1/ws/campaign-test/c1"),             # browser Test agent, self-contained
    ("GET", "/api/v1/admin/calls/live"),               # Postgres-backed
    ("POST", "/api/v1/auth/login"),
    ("DELETE", "/api/v1/campaigns/c1/contacts/x"),
]


@pytest.mark.parametrize("method,uri", CALL)
def test_live_call_routes_reach_the_call_process(method, uri):
    assert _route(method, uri) == "talky_call"


@pytest.mark.parametrize("method,uri", WEB)
def test_everything_else_reaches_the_dashboard_process(method, uri):
    assert _route(method, uri) == "talky_web"


def test_dashboard_upstream_falls_back_to_the_call_process():
    text = NGINX.read_text(encoding="utf-8")
    web = text[text.index("upstream talky_web {"):]
    web = web[: web.index("}")]
    assert "server 127.0.0.1:8001" in web and "server 127.0.0.1:8000 backup;" in web
    call = text[text.index("upstream talky_call {"):]
    assert "server 127.0.0.1:8000;" in call[: call.index("}")]


def test_certbot_tls_lines_are_preserved():
    text = NGINX.read_text(encoding="utf-8")
    for line in ("ssl_certificate /etc/letsencrypt/live/api.talkleeai.com/fullchain.pem;",
                 "ssl_certificate_key /etc/letsencrypt/live/api.talkleeai.com/privkey.pem;",
                 "include /etc/letsencrypt/options-ssl-nginx.conf;",
                 "ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;"):
        assert line in text


def test_the_routed_paths_still_exist_in_the_code():
    """If a call-control route is renamed, the nginx rule would silently stop
    matching it and send it to the dashboard process."""
    ep = BACKEND / "app" / "api" / "v1" / "endpoints"
    assert 'prefix="/sip/telephony"' in (ep / "telephony_bridge.py").read_text(encoding="utf-8")
    assert 'prefix="/twilio"' in (ep / "twilio_bridge.py").read_text(encoding="utf-8")
    assert 'prefix="/vonage"' in (ep / "vonage_bridge.py").read_text(encoding="utf-8")
    assert '"/{call_id}/hangup"' in (ep / "calls.py").read_text(encoding="utf-8")
    assert '@router.post("/calls/{call_id}/terminate")' in (ep / "admin" / "calls.py").read_text(encoding="utf-8")
    campaigns = (ep / "campaigns.py").read_text(encoding="utf-8")
    assert '@router.post("/{campaign_id}/pause"' in campaigns
    assert '@router.post("/{campaign_id}/stop"' in campaigns
