"""POST /telephony/sip/trunks/{id}/test reports the line's REAL state, step by step.

Owner request 2026-10-14: "test ping fetch real status not mock". The old Test sent
one OPTIONS and said "SIP server answered 404 to OPTIONS; this does not verify call
authorization". The new Test runs four checks in order (address, reachability,
credentials, live registration), stops sending traffic at a hard failure, never
touches a healthy registration, and ends with one plain sentence and a status.

Network probes are replaced here by canned answers; the wire behaviour of the
REGISTER probe is tested against a real UDP socket in
test_sip_trunk_register_probe.py.
"""
from __future__ import annotations

import json
import socket
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from starlette.requests import Request

from app.api.v1.endpoints.telephony_sip import trunk_probe, trunks

TENANT = str(uuid4())
LINE_PW = "plain-text-secret-for-test"
HOST = "sip.provider.test"


def options(code="200", latency=42, **extra):
    return {"ok": True, "latency_ms": latency, "transport": "udp", "target": f"{HOST}:5060",
            "sip_code": code, "detail": f"SIP server answered {code} to OPTIONS; raw", **extra}


def options_silent():
    return {"ok": False, "latency_ms": 5000, "transport": "udp", "target": f"{HOST}:5060",
            "error": "timeout", "timeout_code": 408, "detail": "No SIP response within 5.0s"}


def register(outcome, code=None, **extra):
    return {"outcome": outcome, "sip_code": code, "reason": None, "latency_ms": 30, "detail": outcome, **extra}


class Conn:
    def __init__(self, row):
        self.row, self.writes, self.reads = row, [], []

    @asynccontextmanager
    async def transaction(self):
        yield self

    async def fetchrow(self, sql, *args):
        sql = " ".join(sql.split())
        self.reads.append((sql, args))
        assert "tenant_id" in sql and "deleted_at IS NULL" in sql, sql
        return self.row

    async def execute(self, sql, *args):
        if "set_config" in sql:
            return "SELECT 1"
        self.writes.append((" ".join(sql.split()), args))
        return "UPDATE 1"


def trunk_row(**overrides):
    row = {
        "sip_domain": HOST, "port": 5060, "transport": "udp",
        "auth_username": "150001", "auth_password_encrypted": "enc:" + LINE_PW,
        "metadata": {"register": True}, "is_active": False,
        "live_registration_status": "inactive", "live_status_detail": None,
        "live_status_checked_at": None, "last_tested_at": None,
    }
    row.update(overrides)
    return row


@pytest.fixture(autouse=True)
def env(monkeypatch):
    trunks._test_history.clear()
    state = SimpleNamespace(conn=Conn(trunk_row()), register=AsyncMock(), options=AsyncMock(), trunk_id=uuid4())
    state.register.return_value = register("accepted", 200)
    state.options.return_value = options()

    @asynccontextmanager
    async def acquire(_pool, tenant_id, **_kwargs):
        assert str(tenant_id) == TENANT
        yield state.conn

    async def resolve(*, host, port, socktype):
        assert host in {HOST, "proxy.provider.test"}
        return socket.AF_INET, ("203.0.113.9", port)

    monkeypatch.setattr(trunks, "acquire_with_tenant", acquire)
    monkeypatch.setattr(trunks, "get_encryption_service",
                        lambda: SimpleNamespace(decrypt=lambda enc: enc.removeprefix("enc:")))
    monkeypatch.setattr(trunks, "_trunk_test_redis", lambda: None)
    monkeypatch.setattr(trunk_probe, "resolve_sip_target", resolve)
    monkeypatch.setattr(trunk_probe, "probe_sip_endpoint", state.options)
    monkeypatch.setattr(trunk_probe, "probe_sip_register", state.register)
    return state


async def run_test(env, **overrides):
    env.conn.row = trunk_row(**overrides)
    request = Request({"type": "http", "method": "POST", "path": "/trunks/x/test", "headers": []})
    user = SimpleNamespace(tenant_id=TENANT, id=str(uuid4()))
    return await trunks.test_sip_trunk(env.trunk_id, request, user, object())


def steps_by_key(result):
    return {step.key: step for step in result.steps}


def fresh(status, detail=None, age=3):
    return {"is_active": True, "live_registration_status": status, "live_status_detail": detail,
            # Evaluated when the test runs: a module-level "now" goes stale (the freshness
            # window is 60 s) between collection and execution in a long full-suite run.
            "live_status_checked_at": datetime.now(timezone.utc) - timedelta(seconds=age)}


@pytest.mark.asyncio
async def test_a_registered_line_is_connected_and_never_re_registered(env):
    result = await run_test(env, **fresh("registered"))
    steps = steps_by_key(result)
    assert [s.key for s in result.steps] == ["address", "reachability", "credentials", "registration"]
    assert result.status == "connected" and result.ok is True
    # Asterisk's own fresh registration IS the proof: no REGISTER is sent.
    env.register.assert_not_awaited()
    assert steps["credentials"].ok is True and steps["credentials"].detail == "Signed in — the line is registered"
    assert steps["registration"].ok is True
    assert steps["reachability"].detail == "Server reachable (42 ms)" and steps["reachability"].ok is True
    assert steps["address"].ok is True and "203.0.113.9" in steps["address"].detail
    assert "connected" in result.verdict.lower() or "registered" in result.verdict.lower()


@pytest.mark.asyncio
async def test_response_keeps_every_legacy_field_and_the_card_shows_the_verdict(env):
    result = await run_test(env, **fresh("registered"))
    body = result.model_dump()
    for legacy in ("ok", "latency_ms", "transport", "target", "error", "detail", "tested_at", "sip_code", "timeout_code"):
        assert legacy in body
    assert (result.latency_ms, result.transport.value, result.target, result.sip_code) == (42, "udp", f"{HOST}:5060", "200")
    # The Settings card prints last_test_result.detail: it must be the plain sentence.
    assert result.detail == result.verdict and "answered 200 to OPTIONS" not in result.detail
    assert [set(s) for s in body["steps"]] == [{"key", "ok", "label", "detail", "sip_code"}] * 4


@pytest.mark.asyncio
async def test_a_stale_registered_status_is_not_trusted_and_the_credentials_are_checked(env):
    result = await run_test(env, **fresh("registered", age=600))
    env.register.assert_awaited_once()
    steps = steps_by_key(result)
    assert steps["credentials"].ok is True and "registered" not in steps["credentials"].detail.lower()
    assert steps["registration"].ok is None and "unknown" in steps["registration"].detail.lower()
    # Never claim "not registered" from evidence that is simply out of date.
    assert result.status == "reachable" and result.ok is True


@pytest.mark.asyncio
@pytest.mark.parametrize("code,fragment", [
    (403, "Provider refused sign-in"),
])
async def test_rejected_line_shows_the_provider_reason(env, code, fragment):
    env.register.return_value = register("forbidden", code)
    result = await run_test(env, **fresh("rejected", "403 Forbidden"))
    steps = steps_by_key(result)
    assert result.status == "credentials_rejected" and result.ok is False
    assert steps["credentials"].ok is False and fragment in steps["credentials"].detail
    assert steps["credentials"].sip_code == code
    assert steps["registration"].ok is False and "403 Forbidden" in steps["registration"].detail
    assert fragment in result.verdict


@pytest.mark.asyncio
async def test_phone_system_rejection_wins_even_if_the_probe_could_sign_in(env):
    env.register.return_value = register("accepted", 200)
    result = await run_test(env, **fresh("rejected", "403 Forbidden"))
    assert result.status == "credentials_rejected" and result.ok is False
    assert "403 Forbidden" in result.verdict


@pytest.mark.asyncio
async def test_wrong_password_is_named_plainly(env):
    env.register.return_value = register("rejected", 401)
    result = await run_test(env)
    steps = steps_by_key(result)
    assert result.status == "credentials_rejected"
    assert steps["credentials"].detail.startswith("Wrong username or password")
    assert steps["credentials"].sip_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome,code,status,ok,fragment", [
    ("not_found", 404, "credentials_rejected", False, f"This username doesn't exist on {HOST}"),
    ("server_error", 503, "unreachable", False, "Provider temporarily unavailable (503)"),
    ("timeout", None, "unreachable", False, "The server didn't answer"),
    ("no_challenge", 200, "not_registered", True, "accepted us without a password check"),
    ("stale", 401, "not_registered", True, "fresh"),
    ("unsupported", 401, "not_registered", True, "can't be checked"),
    ("other", 405, "not_registered", True, "405"),
])
async def test_each_provider_answer_has_its_own_status_and_sentence(env, outcome, code, status, ok, fragment):
    env.register.return_value = register(outcome, code)
    result = await run_test(env)
    step = steps_by_key(result)["credentials"]
    assert fragment in step.detail, step.detail
    assert step.sip_code == code
    assert (result.status, result.ok) == (status, ok)
    if outcome in {"no_challenge", "stale", "unsupported", "other"}:
        assert step.ok is None, "inconclusive is neither a pass nor a failure"


@pytest.mark.asyncio
async def test_a_switched_off_line_with_good_credentials_says_how_to_finish(env):
    result = await run_test(env)
    steps = steps_by_key(result)
    assert steps["credentials"].ok is True
    assert steps["registration"].ok is None
    assert steps["registration"].detail == "Line is off — turn it on to register."
    assert result.status == "not_registered" and result.ok is True
    assert "turn" in result.verdict.lower()
    env.register.assert_awaited_once()


@pytest.mark.asyncio
async def test_an_active_line_that_has_not_registered_yet_is_reported_as_such(env):
    result = await run_test(env, **fresh("unregistered"))
    steps = steps_by_key(result)
    assert steps["registration"].ok is False and "Not registered yet" in steps["registration"].detail
    assert result.status == "not_registered" and result.ok is False


@pytest.mark.asyncio
async def test_ip_authenticated_line_has_no_credentials_step_to_run(env):
    result = await run_test(env, auth_username=None, auth_password_encrypted=None, metadata={"register": False})
    env.register.assert_not_awaited()
    steps = steps_by_key(result)
    assert steps["credentials"].ok is None and "IP" in steps["credentials"].detail
    assert steps["reachability"].ok is True
    assert result.status == "reachable" and result.ok is True


@pytest.mark.asyncio
async def test_an_ip_authenticated_line_the_phone_system_can_reach_is_connected(env):
    result = await run_test(env, auth_username=None, auth_password_encrypted=None,
                            metadata={"register": False}, **fresh("reachable"))
    assert result.status == "connected"
    assert steps_by_key(result)["registration"].ok is True


@pytest.mark.asyncio
async def test_an_ip_line_that_is_loaded_but_not_yet_qualified_is_reachable_not_unknown(env):
    result = await run_test(env, auth_username=None, auth_password_encrypted=None,
                            metadata={"register": False}, **fresh("loaded"))
    registration = steps_by_key(result)["registration"]
    assert registration.ok is None and "loaded" in registration.detail
    assert result.status == "reachable" and result.ok is True
    assert "hasn't reported" not in result.verdict, "the phone system did report; it just has no proof yet"


@pytest.mark.asyncio
async def test_only_a_real_registration_replaces_the_credentials_check(env):
    # Live "reachable" means Asterisk qualified an IP-authenticated endpoint; it is not
    # proof that the saved username and password sign in, so the probe still runs.
    result = await run_test(env, metadata={"register": False}, **fresh("reachable"))
    env.register.assert_awaited_once()
    steps = steps_by_key(result)
    assert steps["credentials"].detail.startswith("Username and password accepted")
    assert result.status == "connected"


@pytest.mark.asyncio
async def test_unknown_host_stops_before_any_traffic_is_sent(env, monkeypatch):
    async def missing(**_):
        raise socket.gaierror("no such host")

    monkeypatch.setattr(trunk_probe, "resolve_sip_target", missing)
    result = await run_test(env)
    steps = steps_by_key(result)
    assert steps["address"].ok is False and steps["address"].detail == f"We can't find {HOST}. Check the spelling."
    assert steps["reachability"].ok is None and steps["credentials"].ok is None
    env.options.assert_not_awaited()
    env.register.assert_not_awaited()
    assert result.status == "unreachable" and result.ok is False and result.verdict == steps["address"].detail
    assert result.error == "dns_failure"


@pytest.mark.asyncio
async def test_a_private_address_is_reported_without_probing(env, monkeypatch):
    async def private(**_):
        raise PermissionError("SIP target resolves to a non-public network address")

    monkeypatch.setattr(trunk_probe, "resolve_sip_target", private)
    result = await run_test(env)
    assert steps_by_key(result)["address"].ok is False
    assert "private" in steps_by_key(result)["address"].detail.lower()
    assert result.status == "unreachable" and result.error == "unsafe_target"
    env.options.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_silent_server_stops_the_checks_and_keeps_the_legacy_timeout_fields(env):
    env.options.return_value = options_silent()
    result = await run_test(env)
    steps = steps_by_key(result)
    assert steps["reachability"].ok is False and "The server didn't answer" in steps["reachability"].detail
    assert steps["credentials"].ok is None, "no REGISTER is sent to a server that ignored OPTIONS"
    env.register.assert_not_awaited()
    assert result.status == "unreachable" and result.ok is False
    assert (result.error, result.timeout_code, result.latency_ms) == ("timeout", 408, 5000)


@pytest.mark.asyncio
async def test_a_registered_line_stays_connected_even_if_our_options_probe_gets_no_answer(env):
    # Asterisk's fresh registration is ground truth; some providers ignore OPTIONS.
    env.options.return_value = options_silent()
    result = await run_test(env, **fresh("registered"))
    assert result.status == "connected" and result.ok is True
    assert steps_by_key(result)["reachability"].ok is False, "the failed step is still shown"


@pytest.mark.asyncio
async def test_options_refused_with_403_means_the_provider_must_allow_our_server(env):
    env.options.return_value = options("403")
    env.register.return_value = register("forbidden", 403)
    result = await run_test(env)
    reach = steps_by_key(result)["reachability"]
    assert reach.ok is True and reach.sip_code == 403
    assert "refusing our server" in reach.detail and "allow-list" in reach.detail
    assert result.status == "credentials_rejected"


@pytest.mark.asyncio
async def test_any_other_sip_answer_to_options_counts_as_reachable_and_keeps_the_code(env):
    env.options.return_value = options("404", latency=77)
    reach = steps_by_key(await run_test(env))["reachability"]
    assert reach.ok is True and reach.detail == "Server reachable (77 ms)" and reach.sip_code == 404


@pytest.mark.asyncio
async def test_tcp_and_tls_lines_get_the_live_status_instead_of_a_register_probe(env):
    result = await run_test(env, transport="tls")
    env.register.assert_not_awaited()
    cred = steps_by_key(result)["credentials"]
    assert cred.ok is None and "TLS" in cred.detail


@pytest.mark.asyncio
async def test_an_unreadable_saved_password_is_reported_not_raised(env, monkeypatch):
    def broken(_enc):
        raise ValueError("bad key")

    monkeypatch.setattr(trunks, "get_encryption_service", lambda: SimpleNamespace(decrypt=broken))
    result = await run_test(env)
    cred = steps_by_key(result)["credentials"]
    assert cred.ok is False and "saved password" in cred.detail
    env.register.assert_not_awaited()
    # The line cannot sign in whatever the provider says, and the sentence says how to fix it.
    assert (result.status, result.ok, result.error) == ("credentials_rejected", False, "credentials_unreadable")
    assert result.verdict == cred.detail


@pytest.mark.asyncio
async def test_the_probe_gets_the_trunks_own_domain_and_credentials(env):
    result = await run_test(env)
    kwargs = env.register.await_args.kwargs
    assert (kwargs["username"], kwargs["password"], kwargs["domain"], kwargs["port"]) == ("150001", LINE_PW, HOST, 5060)
    assert kwargs["proxy"] is None
    assert (env.options.await_args.kwargs["host"], env.options.await_args.kwargs["port"]) == (HOST, 5060)
    assert result.target == f"{HOST}:5060"


@pytest.mark.asyncio
async def test_with_an_outbound_proxy_the_checks_go_where_asterisk_sends_traffic(env):
    result = await run_test(env, metadata={"register": True, "outbound_proxy": "proxy.provider.test:5070"})
    kwargs = env.register.await_args.kwargs
    # The account is still named by the SIP domain; only the destination changes.
    assert (kwargs["domain"], kwargs["port"], kwargs["proxy"]) == (HOST, 5060, ("proxy.provider.test", 5070))
    assert (env.options.await_args.kwargs["host"], env.options.await_args.kwargs["port"]) == ("proxy.provider.test", 5070)
    assert result.target == "proxy.provider.test:5070"
    assert "proxy.provider.test" in steps_by_key(result)["address"].detail


@pytest.mark.asyncio
async def test_the_password_is_never_in_the_response_or_the_saved_result(env):
    env.register.return_value = register("rejected", 401, detail=f"echo {LINE_PW}")
    result = await run_test(env)
    assert LINE_PW not in result.model_dump_json()
    ((sql, args),) = [w for w in env.conn.writes if "last_test_result" in w[0]]
    assert "deleted_at IS NULL" in sql and "tenant_id" in sql
    saved = json.dumps(args, default=str)
    assert LINE_PW not in saved
    stored = next(a for a in args if isinstance(a, dict))
    assert [s["key"] for s in stored["steps"]] == ["address", "reachability", "credentials", "registration"]
    assert stored["status"] == "credentials_rejected" and stored["verdict"] == stored["detail"]


@pytest.mark.asyncio
async def test_a_missing_or_deleted_line_is_404_and_nothing_is_probed(env):
    env.conn.row = None
    request = Request({"type": "http", "method": "POST", "path": "/trunks/x/test", "headers": []})
    response = await trunks.test_sip_trunk(uuid4(), request, SimpleNamespace(tenant_id=TENANT, id="u"), object())
    assert response.status_code == 404
    env.options.assert_not_awaited()
    assert not env.conn.writes


@pytest.mark.asyncio
async def test_a_second_test_within_ten_seconds_is_refused_with_a_plain_message(env):
    first = await run_test(env)
    assert first.status
    response = await run_test(env, last_tested_at=datetime.now(timezone.utc) - timedelta(seconds=3))
    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) in range(1, 11)
    assert "wait" in json.loads(response.body)["detail"].lower()
    assert env.options.await_count == 1, "the refused click sent nothing to the provider"


@pytest.mark.asyncio
async def test_the_eleventh_test_in_fifteen_minutes_is_refused(env):
    for _ in range(10):
        assert getattr(await run_test(env), "status", None) is not None
    refused = await run_test(env)
    assert refused.status_code == 429
    assert "10 times" in json.loads(refused.body)["detail"]
    assert int(refused.headers["Retry-After"]) > 10
    assert env.options.await_count == 10, "refused clicks are not counted and send nothing"


@pytest.mark.asyncio
async def test_the_fifteen_minute_window_slides(env, monkeypatch):
    clock = {"now": 1_000_000.0}
    monkeypatch.setattr(trunks, "_clock", lambda: clock["now"])
    trunk = uuid4()

    async def one():
        env.conn.row = trunk_row()
        request = Request({"type": "http", "method": "POST", "path": "/t", "headers": []})
        return await trunks.test_sip_trunk(trunk, request, SimpleNamespace(tenant_id=TENANT, id="u"), object())

    for _ in range(10):
        clock["now"] += 20
        assert getattr(await one(), "status", None)
    clock["now"] += 20
    assert (await one()).status_code == 429
    clock["now"] += 15 * 60  # the oldest tests have aged out
    assert getattr(await one(), "status", None)


@pytest.mark.asyncio
async def test_the_limit_is_per_line_not_shared(env):
    for _ in range(10):
        await run_test(env)
    request = Request({"type": "http", "method": "POST", "path": "/t", "headers": []})
    env.conn.row = trunk_row()
    other = await trunks.test_sip_trunk(uuid4(), request, SimpleNamespace(tenant_id=TENANT, id="u"), object())
    assert getattr(other, "status", None)


@pytest.mark.asyncio
async def test_redis_backs_the_counter_when_it_is_available(env, monkeypatch):
    import fakeredis.aioredis

    redis = fakeredis.aioredis.FakeRedis()
    monkeypatch.setattr(trunks, "_trunk_test_redis", lambda: redis)
    trunk = uuid4()

    async def one():
        env.conn.row = trunk_row()
        request = Request({"type": "http", "method": "POST", "path": "/t", "headers": []})
        return await trunks.test_sip_trunk(trunk, request, SimpleNamespace(tenant_id=TENANT, id="u"), object())

    for _ in range(10):
        assert getattr(await one(), "status", None)
    assert (await one()).status_code == 429
    assert not trunks._test_history, "the in-memory fallback is not used while Redis answers"
    keys = [k async for k in redis.scan_iter("*")]
    assert len(keys) == 1 and TENANT in keys[0].decode() and str(trunk) in keys[0].decode()


@pytest.mark.asyncio
async def test_a_redis_outage_falls_back_to_the_in_memory_counter(env, monkeypatch):
    class Broken:
        def __getattr__(self, name):
            async def fail(*a, **k):
                raise ConnectionError("redis down")
            return fail

    monkeypatch.setattr(trunks, "_trunk_test_redis", lambda: Broken())
    trunk = uuid4()
    for _ in range(10):
        env.conn.row = trunk_row()
        request = Request({"type": "http", "method": "POST", "path": "/t", "headers": []})
        assert getattr(await trunks.test_sip_trunk(trunk, request, SimpleNamespace(tenant_id=TENANT, id="u"), object()), "status", None)
    env.conn.row = trunk_row()
    refused = await trunks.test_sip_trunk(trunk, request, SimpleNamespace(tenant_id=TENANT, id="u"), object())
    assert refused.status_code == 429
