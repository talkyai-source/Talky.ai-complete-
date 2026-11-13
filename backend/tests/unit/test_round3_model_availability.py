"""F11-02 nightly model discovery and call-consumed availability contracts.

All HTTP, credentials and database rows are synthetic. Unit orchestration is
not evidence that a real provider catalog or Ubuntu timer was exercised.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from copy import deepcopy
import importlib
import importlib.util
import json
from pathlib import Path
import re
import sys
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import UUID

from fakeredis.aioredis import FakeRedis
import httpx
import pytest

from app.domain.services.credential_resolver import ResolvedCredential
from tests.unit.test_round3_model_selection import DEFAULTS, SECRET, TENANT, runtime  # noqa: F401


BACKEND = Path(__file__).resolve().parents[2]
SERVICE = BACKEND / "app" / "domain" / "services" / "model_availability.py"
SCRIPT = BACKEND / "scripts" / "check_saved_models.py"
OTHER = "11111111-2222-4333-8444-666666666666"
NOW = 1793923200.0


@pytest.fixture
def availability():
    assert SERVICE.is_file(), "F11-02 is missing credential-scoped model availability"
    return importlib.import_module("app.domain.services.model_availability")


@pytest.fixture
def script(availability):
    assert SCRIPT.is_file(), "F11-02 is missing the bounded nightly saved-model report"
    spec = importlib.util.spec_from_file_location("round3_check_saved_models", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
async def cache():
    value = FakeRedis(decode_responses=True)
    yield value
    await value.aclose()


def credential(tenant=TENANT, secret=SECRET):
    return ResolvedCredential(secret, source="tenant", tenant_id=tenant,
                              credential_id="bbbbbbbb-2222-4333-8444-555555555555")


async def snapshot(service, cache, *, tenant=TENANT, secret=SECRET, models=None, checked_at=NOW):
    await service.store_snapshot(
        cache, tenant_id=tenant, provider="groq", credential=credential(tenant, secret),
        models=models if models is not None else {DEFAULTS["groq"]}, checked_at=checked_at,
    )


async def test_fresh_same_credential_snapshot_marks_known_legacy_model_retired(availability, cache):
    await snapshot(availability, cache)
    selected = await availability.resolve_call_model(
        provider="groq", model="openai/gpt-oss-120b", tenant_id=TENANT,
        credential=credential(), cache=cache, now=NOW,
    )
    assert selected.model == DEFAULTS["groq"] and selected.reason == "retired_model"


@pytest.mark.parametrize("mismatch", ["tenant", "rotated_key", "stale", "missing"])
async def test_absent_stale_or_different_credential_catalog_is_not_retirement(availability, cache, mismatch):
    if mismatch != "missing":
        await snapshot(availability, cache, checked_at=NOW - 3 * 86400 if mismatch == "stale" else NOW)
    tenant = OTHER if mismatch == "tenant" else TENANT
    secret = "rotated-synthetic-key" if mismatch == "rotated_key" else SECRET
    selected = await availability.resolve_call_model(
        provider="groq", model="openai/gpt-oss-120b", tenant_id=tenant,
        credential=credential(tenant, secret), cache=cache, now=NOW,
    )
    assert selected.model == "openai/gpt-oss-120b" and selected.reason is None


async def test_available_legacy_model_is_not_remapped_and_cache_never_contains_key(availability, cache):
    await snapshot(availability, cache, models={DEFAULTS["groq"], "openai/gpt-oss-120b"})
    selected = await availability.resolve_call_model(
        provider="groq", model="openai/gpt-oss-120b", tenant_id=TENANT,
        credential=credential(), cache=cache, now=NOW,
    )
    assert selected.model == "openai/gpt-oss-120b" and selected.reason is None
    keys = await cache.keys("*")
    assert len(keys) == 1
    assert SECRET not in keys[0] + await cache.get(keys[0])
    assert 0 < await cache.ttl(keys[0]) <= 2 * 86400


@pytest.mark.parametrize("contents", ["not-json", "[]", '{"models": []}', '{"checked_at": 1e300,"models":["x"]}'])
async def test_malformed_or_impossible_snapshot_is_unknown(availability, cache, contents):
    await snapshot(availability, cache)
    key = (await cache.keys("*"))[0]
    await cache.set(key, contents)
    chosen = await availability.resolve_call_model(
        provider="groq", model="openai/gpt-oss-120b", tenant_id=TENANT,
        credential=credential(), cache=cache, now=NOW,
    )
    assert chosen.model == "openai/gpt-oss-120b" and chosen.reason is None


async def test_cache_read_failure_cannot_turn_every_saved_model_into_retired(availability):
    broken = NS(get=AsyncMock(side_effect=RuntimeError(SECRET)))
    result = await availability.resolve_call_model(
        provider="groq", model="openai/gpt-oss-120b", tenant_id=TENANT,
        credential=credential(), cache=broken, now=NOW,
    )
    assert result.model == "openai/gpt-oss-120b" and result.reason is None


async def test_actual_runtime_consumes_fresh_snapshot_without_a_provider_list_call(availability, cache, runtime, monkeypatch):
    from app.domain.services.voice_orchestrator import VoiceSessionConfig

    await snapshot(availability, cache)
    monkeypatch.setattr(availability, "get_model_cache", lambda: cache)
    monkeypatch.setattr(availability.time, "time", lambda: NOW)
    monkeypatch.setattr(availability, "fetch_provider_models", AsyncMock(side_effect=AssertionError("Call startup queried provider catalog")))
    config = VoiceSessionConfig(tenant_id=TENANT, llm_provider_type="groq", llm_model="openai/gpt-oss-120b")
    await runtime.orchestrator._create_llm_provider(config)
    assert runtime.provider.initialize.await_args.args[0]["model"] == DEFAULTS["groq"]
    assert config.llm_model == "openai/gpt-oss-120b"
    assert len(runtime.pool.conn.receipts) == 1


class HTTP:
    """Strict scripted transport, not a real httpx client."""
    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []

    async def get(self, url, *, headers=None, params=None, timeout=None, **kwargs):
        assert isinstance(timeout, (int, float)) and 0 < timeout <= 10
        assert kwargs.get("follow_redirects", False) is False
        request = httpx.Request("GET", url, headers=headers, params=params)
        assert request.url.scheme == "https"
        assert request.url.host in {
            "api.groq.com", "api.cerebras.ai", "api.openai.com", "api.deepseek.com",
            "generativelanguage.googleapis.com",
        }
        assert SECRET not in str(request.url), "Credentials must stay out of URL/query logs"
        self.requests.append(request)
        assert self.replies, "Catalog request/page limit exceeded the scripted bound"
        value = self.replies.pop(0)
        if isinstance(value, BaseException):
            raise value
        status, data = value
        return httpx.Response(status, json=data, request=request)

    @asynccontextmanager
    async def stream(self, method, url, **kwargs):
        assert method == "GET"
        response = await self.get(url, **kwargs)
        try:
            yield response
        finally:
            await response.aclose()


@pytest.mark.parametrize("provider", ["groq", "cerebras", "openai", "deepseek"])
async def test_openai_compatible_lists_are_authenticated_and_read_only(availability, provider):
    http = HTTP([(200, {"data": [{"id": DEFAULTS[provider]}]})])
    result = await availability.fetch_provider_models(http, provider=provider, credential=credential())
    assert result == frozenset({DEFAULTS[provider]})
    assert http.requests[0].headers["authorization"] == f"Bearer {SECRET}"
    assert http.requests[0].method == "GET" and http.requests[0].url.path.endswith("/models")


async def test_gemini_list_finishes_pages_and_keeps_only_generation_models(availability):
    http = HTTP([
        (200, {"models": [{"name": "models/embedding-only", "supportedGenerationMethods": ["embedContent"]}],
               "nextPageToken": "synthetic-next-page"}),
        (200, {"models": [{"name": "models/gemini-3.8-flash", "supportedGenerationMethods": ["generateContent"]}]}),
    ])
    models = await availability.fetch_provider_models(http, provider="gemini", credential=credential())
    assert models == frozenset({"gemini-3.8-flash"})
    assert len(http.requests) == 2
    assert all(request.headers["x-goog-api-key"] == SECRET for request in http.requests)
    assert http.requests[1].url.params["pageToken"] == "synthetic-next-page"


@pytest.mark.parametrize("status,data", [
    (401, {"error": SECRET}), (429, {"error": SECRET}), (500, {"error": SECRET}),
    (200, {"wrong": "shape"}), (200, {"data": []}), (200, {"data": [{"no-id": "x"}]}),
    (200, {"data": [{"id": "x"}], "has_more": True}),
])
async def test_failed_empty_or_incomplete_list_cannot_be_authoritative_retirement(availability, status, data):
    with pytest.raises(availability.CatalogUnavailable) as error:
        await availability.fetch_provider_models(HTTP([(status, data)]), provider="groq", credential=credential())
    assert SECRET not in str(error.value)


async def test_gemini_repeating_token_stops_without_unbounded_requests(availability):
    page = {"models": [{"name": "models/gemini-3.8-flash", "supportedGenerationMethods": ["generateContent"]}],
            "nextPageToken": "repeated"}
    http = HTTP([(200, page), (200, page)])
    with pytest.raises(availability.CatalogUnavailable):
        await availability.fetch_provider_models(http, provider="gemini", credential=credential())
    assert len(http.requests) == 2


async def test_oversized_catalog_is_refused_before_snapshot_can_be_published(availability):
    http = HTTP([(200, {"data": [{"id": "x" * 1_100_000}]})])
    with pytest.raises(availability.CatalogUnavailable):
        await availability.fetch_provider_models(http, provider="groq", credential=credential())


class ProfilePool:
    def __init__(self, rows):
        self.rows = {str(row["tenant_id"]): dict(row) for row in rows}
        self.original = deepcopy(self.rows)
        self.tenant = None
        self.depth = 0
        self.queries = []

    @asynccontextmanager
    async def acquire(self, **_kwargs):
        yield self

    @asynccontextmanager
    async def transaction(self, **_kwargs):
        old = self.tenant
        self.depth += 1
        try:
            yield self
        finally:
            self.tenant = old
            self.depth -= 1

    async def execute(self, query, *args):
        flat = " ".join(query.split())
        assert self.depth
        if re.fullmatch(r"SET LOCAL (statement_timeout|lock_timeout) = '[1-9][0-9]*ms'", flat):
            return "SET"
        if flat == "SET LOCAL app.bypass_rls = 'on'":
            return "SET"
        if flat.startswith("SET LOCAL app.current_tenant_id = '"):
            self.tenant = str(UUID(flat.split("'")[1]))
            return "SET"
        raise AssertionError(f"Nightly report attempted unsupported SQL/write: {flat}")

    async def fetch(self, query, *args):
        flat = " ".join(query.split())
        self.queries.append((flat, args))
        assert "FROM tenants" in flat and "ORDER BY id" in flat and "LIMIT" in flat
        cursor, limit = args
        assert 0 < limit <= 100
        return [{"id": UUID(value)} for value in sorted(self.rows)
                if cursor is None or UUID(value) > UUID(str(cursor))][:limit]

    async def fetchrow(self, query, *args):
        flat = " ".join(query.split())
        self.queries.append((flat, args))
        assert "FROM tenant_ai_configs" in flat and re.search(r"WHERE tenant_id\s*=\s*\$1", flat)
        assert str(args[0]) == self.tenant
        return deepcopy(self.rows.get(str(args[0])))


def profile(tenant=TENANT, model="openai/gpt-oss-120b", mode="cascaded", provider="groq"):
    return {"tenant_id": tenant, "llm_provider": provider, "llm_model": model, "pipeline_mode": mode}


async def test_nightly_accounts_are_separate_and_profiles_are_never_remapped(script, availability, cache):
    pool = ProfilePool([profile(), profile(OTHER)])
    resolver = NS(resolve_with_metadata=AsyncMock(side_effect=lambda _provider, **kw: credential(kw["tenant_id"], SECRET + kw["tenant_id"])))
    http = HTTP([
        (200, {"data": [{"id": DEFAULTS["groq"]}]}),
        (200, {"data": [{"id": DEFAULTS["groq"]}, {"id": "openai/gpt-oss-120b"}]}),
    ])
    result = await script.audit_saved_models(pool, resolver=resolver, cache=cache, client=http, now=NOW, page_size=1)
    assert result["checked"] == 2 and result["complete"] is True
    assert [row["tenant_id"] for row in result["retired"]] == [TENANT]
    assert result["unknown"] == []
    assert pool.rows == pool.original
    assert len(http.requests) == 2
    assert SECRET not in json.dumps(result)
    assert script.report_exit_code(result) != 0
    for tenant, expected in [(TENANT, DEFAULTS["groq"]), (OTHER, "openai/gpt-oss-120b")]:
        selected = await availability.resolve_call_model(
            provider="groq", model="openai/gpt-oss-120b", tenant_id=tenant,
            credential=credential(tenant, SECRET + tenant), cache=cache, now=NOW,
        )
        assert selected.model == expected


async def test_nightly_transport_failure_is_unknown_and_does_not_replace_good_snapshot(script, availability, cache):
    await snapshot(availability, cache, models={DEFAULTS["groq"], "openai/gpt-oss-120b"})
    pool = ProfilePool([profile()])
    resolver = NS(resolve_with_metadata=AsyncMock(return_value=credential()))
    result = await script.audit_saved_models(
        pool, resolver=resolver, cache=cache,
        client=HTTP([httpx.ReadTimeout(f"provider text {SECRET}")]), now=NOW,
    )
    assert not result["complete"] and result["retired"] == [] and len(result["unknown"]) == 1
    assert SECRET not in json.dumps(result)
    assert script.report_exit_code(result) != 0
    selected = await availability.resolve_call_model(provider="groq", model="openai/gpt-oss-120b",
        tenant_id=TENANT, credential=credential(), cache=cache, now=NOW)
    assert selected.model == "openai/gpt-oss-120b" and selected.reason is None


async def test_nightly_missing_default_is_visible_even_when_saved_model_still_exists(script, cache):
    result = await script.audit_saved_models(
        ProfilePool([profile()]), resolver=NS(resolve_with_metadata=AsyncMock(return_value=credential())),
        cache=cache, client=HTTP([(200, {"data": [{"id": "openai/gpt-oss-120b"}]})]), now=NOW,
    )
    assert result["retired"] == []
    assert len(result["default_unavailable"]) == 1 and script.report_exit_code(result) != 0


async def test_nightly_realtime_is_outside_this_cascaded_remediation(script, cache):
    resolver = NS(resolve_with_metadata=AsyncMock(side_effect=AssertionError("Realtime credential requested")))
    pool = ProfilePool([profile(mode="realtime")])
    http = HTTP([])
    result = await script.audit_saved_models(pool, resolver=resolver, cache=cache, client=http, now=NOW)
    assert result["checked"] == 0 and result["skipped_realtime"] == 1
    assert result["retired"] == result["unknown"] == []
    assert http.requests == [] and pool.rows == pool.original
    assert script.report_exit_code(result) == 0


async def test_nightly_tenant_cap_is_reported_incomplete_not_silently_green(script, cache):
    pool = ProfilePool([profile(), profile(OTHER)])
    result = await script.audit_saved_models(
        pool, resolver=NS(resolve_with_metadata=AsyncMock(return_value=credential())), cache=cache,
        client=HTTP([(200, {"data": [{"id": DEFAULTS["groq"]}]} )]),
        now=NOW, page_size=1, max_tenants=1,
    )
    assert not result["complete"] and result["checked"] == 1
    assert script.report_exit_code(result) != 0


async def test_platform_pool_checks_each_distinct_configured_key_without_advancing_rotation(script, availability, cache, monkeypatch):
    from app.infrastructure.providers.key_pool import PlatformKeyRotation

    keys = [SECRET + suffix for suffix in ("-a", "-b", "-c")]
    monkeypatch.setenv("DEEPSEEK_API_KEY", keys[0])
    monkeypatch.setenv("DEEPSEEK_API_KEYS", f" {keys[1]}, {keys[0]}, {keys[2]} ")
    monkeypatch.setattr(PlatformKeyRotation, "next_key", lambda *a, **k: pytest.fail("Nightly enumeration advanced runtime rotation"))
    resolver = NS(resolve_with_metadata=AsyncMock(return_value=ResolvedCredential(keys[0], source="platform", tenant_id=TENANT)))
    http = HTTP([
        (200, {"data": [{"id": DEFAULTS["deepseek"]}]}),
        (200, {"data": [{"id": "deepseek-v4-pro"}]}),
        (200, {"data": [{"id": DEFAULTS["deepseek"]}]}),
    ])
    result = await script.audit_saved_models(ProfilePool([profile(provider="deepseek", model=DEFAULTS["deepseek"])]), resolver=resolver,
                                             cache=cache, client=http, now=NOW)
    assert len(http.requests) == 3, "Nightly report certified only one rotating platform key"
    assert result["checked"] == 1 and result["credentials_checked"] == 3 and result["complete"]
    assert [request.headers["authorization"] for request in http.requests] == [f"Bearer {key}" for key in keys]
    assert len(result["retired"]) == 1
    assert re.fullmatch(r"[0-9a-f]{64}", result["retired"][0]["credential_fingerprint"])
    assert script.report_exit_code(result) != 0
    for key, expected_reason in zip(keys, [None, "retired_model", None]):
        selected = await availability.resolve_call_model(
            provider="deepseek", model=DEFAULTS["deepseek"], tenant_id=TENANT,
            credential=ResolvedCredential(key, source="platform", tenant_id=TENANT), cache=cache, now=NOW,
        )
        assert selected.model == DEFAULTS["deepseek"] and selected.reason == expected_reason
    all_cache = repr([(key, await cache.get(key)) for key in await cache.keys("*")])
    assert all(key not in json.dumps(result) + all_cache for key in keys)


async def test_platform_pool_over_sixteen_is_explicitly_incomplete_and_work_stays_bounded(script, cache, monkeypatch):
    keys = [f"synthetic-pool-key-{index}" for index in range(17)]
    monkeypatch.setenv("DEEPSEEK_API_KEY", keys[0])
    monkeypatch.setenv("DEEPSEEK_API_KEYS", ",".join(keys[1:]))
    resolver = NS(resolve_with_metadata=AsyncMock(return_value=ResolvedCredential(keys[0], source="platform", tenant_id=TENANT)))
    http = HTTP([(200, {"data": [{"id": DEFAULTS["deepseek"]}]}) for _ in range(16)])
    result = await script.audit_saved_models(ProfilePool([profile(provider="deepseek", model=DEFAULTS["deepseek"])]),
                                             resolver=resolver, cache=cache, client=http, now=NOW)
    assert result["complete"] is False, "An excess platform key was silently left uncertified"
    assert result["credentials_checked"] == len(http.requests) == 16
    assert any(row.get("reason") == "credential_limit_reached" for row in result["unknown"])
    assert script.report_exit_code(result) != 0
    assert all(key not in json.dumps(result) for key in keys)


async def test_platform_pool_is_not_probed_for_a_tenant_byok_profile(script, cache, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-platform-key")
    monkeypatch.setenv("DEEPSEEK_API_KEYS", ",".join(f"synthetic-pool-{index}" for index in range(17)))
    http = HTTP([(200, {"data": [{"id": DEFAULTS["deepseek"]}]})])
    result = await script.audit_saved_models(
        ProfilePool([profile(provider="deepseek", model=DEFAULTS["deepseek"])]),
        resolver=NS(resolve_with_metadata=AsyncMock(return_value=credential())),
        cache=cache, client=http, now=NOW,
    )
    assert result["complete"] and result["credentials_checked"] == 1
    assert len(http.requests) == 1 and http.requests[0].headers["authorization"] == f"Bearer {SECRET}"
    assert result["unknown"] == [] and script.report_exit_code(result) == 0


async def test_platform_pool_one_failed_key_is_unknown_without_overwriting_other_key_evidence(script, availability, cache, monkeypatch):
    keys = [SECRET + "-good", SECRET + "-failed"]
    monkeypatch.setenv("DEEPSEEK_API_KEY", keys[0])
    monkeypatch.setenv("DEEPSEEK_API_KEYS", keys[1])
    resolver = NS(resolve_with_metadata=AsyncMock(return_value=ResolvedCredential(keys[0], source="platform", tenant_id=TENANT)))
    http = HTTP([(200, {"data": [{"id": "deepseek-v4-pro"}]}), (500, {"error": keys[1]})])
    result = await script.audit_saved_models(ProfilePool([profile(provider="deepseek", model=DEFAULTS["deepseek"])]), resolver=resolver,
                                             cache=cache, client=http, now=NOW)
    assert result["complete"] is False and result["credentials_checked"] == 2
    assert len(result["unknown"]) == len(result["retired"]) == 1
    assert all(key not in json.dumps(result) for key in keys)
    for key, expected_reason in zip(keys, ["retired_model", None]):
        selected = await availability.resolve_call_model(
            provider="deepseek", model=DEFAULTS["deepseek"], tenant_id=TENANT,
            credential=ResolvedCredential(key, source="platform", tenant_id=TENANT), cache=cache, now=NOW,
        )
        assert selected.model == DEFAULTS["deepseek"] and selected.reason == expected_reason


def test_nightly_timer_runs_the_hardened_report_not_a_profile_rewriter():
    unit = BACKEND / "systemd" / "talky-model-catalog.service"
    timer = BACKEND / "systemd" / "talky-model-catalog.timer"
    assert unit.is_file() and timer.is_file(), "F11-02 has no nightly model-catalog schedule"
    text = unit.read_text(encoding="utf-8")
    schedule = timer.read_text(encoding="utf-8")
    assert "Type=oneshot" in text and "User=talky" in text
    assert "scripts/check_saved_models.py" in text
    assert "TimeoutStartSec=" in text and "NoNewPrivileges=yes" in text
    assert "ProtectSystem=strict" in text
    assert "OnCalendar=" in schedule and "Persistent=true" in schedule

