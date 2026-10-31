"""Platform key pools for AssemblyAI and DeepSeek (owner request 2026-10-19).

The platform's own keys for a provider are ``<NAME>`` plus the comma-separated
``<NAME>S``. Each new session takes the next key that is not benched; a key the
provider refuses for auth, balance or rate limit is benched for every later
session and the request moves to another key. A tenant's own key is never
benched or swapped.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from app.domain.models.conversation import Message, MessageRole
from app.domain.services.credential_resolver import resolve_sync_env_only
from app.infrastructure.llm.deepseek import DeepSeekLLMProvider
from app.infrastructure.providers import key_pool
from app.infrastructure.providers.key_pool import platform_key_rotation
from app.infrastructure.providers.provider_concurrency import ProviderConcurrencyGuard
from app.infrastructure.stt import assemblyai as adapter
from app.infrastructure.stt.assemblyai import AssemblyAISTTProvider

from tests.unit.test_assemblyai_adapter import FakeSocket


@pytest.fixture(autouse=True)
def _fresh_rotations(monkeypatch):
    monkeypatch.setattr(key_pool, "_ROTATIONS", {})
    for name in ("ASSEMBLYAI_API_KEY", "ASSEMBLYAI_API_KEYS", "DEEPSEEK_API_KEY", "DEEPSEEK_API_KEYS"):
        monkeypatch.delenv(name, raising=False)


# ── rotation ────────────────────────────────────────────────────────────────

def test_sessions_rotate_over_the_single_key_and_the_pool_without_duplicates(monkeypatch):
    monkeypatch.setenv("ASSEMBLYAI_API_KEY", " k1 ")
    monkeypatch.setenv("ASSEMBLYAI_API_KEYS", "k1, k2,k3 ,")
    picks = [resolve_sync_env_only("assemblyai") for _ in range(6)]
    assert picks == ["k1", "k2", "k3", "k1", "k2", "k3"]


def test_the_pool_alone_is_enough(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEYS", "sk-a,sk-b")
    assert [resolve_sync_env_only("deepseek") for _ in range(3)] == ["sk-a", "sk-b", "sk-a"]


def test_a_single_key_behaves_exactly_as_before(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "  sk-only  ")
    assert {resolve_sync_env_only("deepseek") for _ in range(3)} == {"sk-only"}
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    assert resolve_sync_env_only("deepseek") is None


def test_other_providers_are_not_rotated_by_the_resolver(monkeypatch):
    # Groq/Deepgram/Cartesia/ElevenLabs keep their own per-instance KeyPool
    # path; the resolver still hands them the single key.
    monkeypatch.setenv("GROQ_API_KEY", "gsk-single")
    monkeypatch.setenv("GROQ_API_KEYS", "gsk-a,gsk-b")
    assert {resolve_sync_env_only("groq") for _ in range(3)} == {"gsk-single"}


def test_a_benched_key_is_skipped_by_later_sessions(monkeypatch):
    monkeypatch.setenv("ASSEMBLYAI_API_KEYS", "k1,k2,k3")
    rotation = platform_key_rotation("ASSEMBLYAI_API_KEY")
    assert rotation.bench("k2", 3600) is True
    assert {resolve_sync_env_only("assemblyai") for _ in range(6)} == {"k1", "k3"}


def test_a_tenant_key_is_never_benched(monkeypatch):
    monkeypatch.setenv("ASSEMBLYAI_API_KEYS", "k1,k2")
    assert platform_key_rotation("ASSEMBLYAI_API_KEY").bench("tenant-own", 3600) is False


def test_when_every_key_is_benched_a_session_still_gets_the_soonest_back(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEYS", "sk-a,sk-b")
    rotation = platform_key_rotation("DEEPSEEK_API_KEY")
    rotation.bench("sk-a", 3600)
    rotation.bench("sk-b", 60)
    assert resolve_sync_env_only("deepseek") == "sk-b"
    assert rotation.next_key(exclude={"sk-b"}, allow_benched=False) is None
    assert rotation.retry_key(exclude=set()) is None


# ── DeepSeek: a refused key moves the request to the next key ─────────────────

def _sse(text):
    return (
        f"data: {json.dumps({'choices': [{'delta': {'content': text}, 'finish_reason': 'stop'}]})}\n\n"
        "data: [DONE]\n\n"
    ).encode()


async def _deepseek(api_key, handler):
    p = DeepSeekLLMProvider()
    await p.initialize({"api_key": api_key, "model": "deepseek-flash"})
    await p._client.close()
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    p._client._http = http
    p._client.chat.completions._http = http
    return p


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 402, 429])
async def test_deepseek_moves_to_the_next_pool_key_when_one_is_refused(monkeypatch, status):
    monkeypatch.setenv("DEEPSEEK_API_KEYS", "sk-empty,sk-funded")
    seen = []

    def handler(request):
        auth = request.headers["authorization"]
        seen.append(auth)
        if auth == "Bearer sk-empty":
            return httpx.Response(status, json={"error": {"message": "Insufficient Balance"}})
        return httpx.Response(200, content=_sse("Hello."))

    p = await _deepseek("sk-empty", handler)
    out = [t async for t in p.stream_chat([Message(role=MessageRole.USER, content="hi")])]
    await p.cleanup()
    assert "".join(out) == "Hello."
    assert seen == ["Bearer sk-empty", "Bearer sk-funded"]
    # Later sessions skip the refused key.
    assert {resolve_sync_env_only("deepseek") for _ in range(3)} == {"sk-funded"}


@pytest.mark.asyncio
async def test_a_refused_request_retries_on_the_first_working_key_not_the_next_empty_one(monkeypatch):
    # Owner's case on 2026-10-19: one funded key plus eight unfunded ones. A
    # session that starts on an unfunded key must cost one extra round trip,
    # not walk through every other unfunded key first.
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-funded")
    monkeypatch.setenv("DEEPSEEK_API_KEYS", "sk-e1,sk-e2,sk-e3")
    seen = []

    def handler(request):
        auth = request.headers["authorization"]
        seen.append(auth)
        if auth != "Bearer sk-funded":
            return httpx.Response(402, json={"error": {"message": "Insufficient Balance"}})
        return httpx.Response(200, content=_sse("Hi."))

    resolve_sync_env_only("deepseek")  # an earlier session moved the rotation on
    p = await _deepseek("sk-e1", handler)
    out = [t async for t in p.stream_chat([Message(role=MessageRole.USER, content="hi")])]
    await p.cleanup()
    assert "".join(out) == "Hi."
    assert seen == ["Bearer sk-e1", "Bearer sk-funded"]


@pytest.mark.asyncio
async def test_deepseek_tenant_key_refusal_is_not_swapped_for_a_platform_key(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEYS", "sk-platform")
    seen = []

    def handler(request):
        seen.append(request.headers["authorization"])
        return httpx.Response(402, json={"error": {"message": "Insufficient Balance"}})

    p = await _deepseek("sk-tenant-own", handler)
    with pytest.raises(RuntimeError, match="DeepSeek chat HTTP 402"):
        _ = [t async for t in p.stream_chat([Message(role=MessageRole.USER, content="x")])]
    await p.cleanup()
    assert set(seen) == {"Bearer sk-tenant-own"}


@pytest.mark.asyncio
async def test_deepseek_gives_up_when_every_pool_key_is_refused(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEYS", "sk-a,sk-b")

    def handler(request):
        return httpx.Response(402, json={"error": {"message": "Insufficient Balance"}})

    p = await _deepseek("sk-a", handler)
    with pytest.raises(RuntimeError, match="DeepSeek chat HTTP 402"):
        _ = [t async for t in p.stream_chat([Message(role=MessageRole.USER, content="x")])]
    await p.cleanup()


# ── AssemblyAI: a refused key moves the connection to the next key ───────────

async def _assemblyai(monkeypatch, api_key, connect):
    provider = AssemblyAISTTProvider()
    provider._guard = ProviderConcurrencyGuard("assemblyai_pool_test", 3)
    monkeypatch.setattr(adapter.websockets, "connect", connect)
    await provider.initialize({"api_key": api_key, "assemblyai_settings": {}})
    return provider


class _Refused(Exception):
    def __init__(self, status):
        super().__init__("private refusal text")
        self.response = SimpleNamespace(status_code=status)


class _Closed(Exception):
    def __init__(self, code):
        super().__init__("private close reason")
        self.rcvd = SimpleNamespace(code=code)


@pytest.mark.parametrize("status", [401, 429])
async def test_assemblyai_handshake_refusal_moves_to_the_next_pool_key(monkeypatch, status):
    monkeypatch.setenv("ASSEMBLYAI_API_KEYS", "aai-dead,aai-live")
    used = []

    async def connect(url, **kwargs):
        key = kwargs["additional_headers"]["Authorization"]
        used.append(key)
        if key == "aai-dead":
            raise _Refused(status)
        return FakeSocket()

    provider = await _assemblyai(monkeypatch, "aai-dead", connect)
    await provider.pre_connect("call")
    assert used == ["aai-dead", "aai-live"]
    assert provider._guard.in_flight == 1  # one live socket, no leaked permit
    await provider.cleanup()
    assert {resolve_sync_env_only("assemblyai") for _ in range(3)} == {"aai-live"}


async def test_assemblyai_account_closure_on_begin_moves_to_the_next_pool_key(monkeypatch):
    # v3 closes with 1008 for an invalid key, no balance or a disabled account.
    monkeypatch.setenv("ASSEMBLYAI_API_KEYS", "aai-empty,aai-live")
    used = []

    async def connect(url, **kwargs):
        key = kwargs["additional_headers"]["Authorization"]
        used.append(key)
        if key == "aai-empty":
            ws = FakeSocket(begin=False)
            ws.incoming.put_nowait(_Closed(1008))
            return ws
        return FakeSocket()

    provider = await _assemblyai(monkeypatch, "aai-empty", connect)
    await provider.pre_connect("call")
    assert used == ["aai-empty", "aai-live"]
    await provider.cleanup()
    assert provider._guard.in_flight == 0


async def test_assemblyai_tenant_key_refusal_is_not_swapped(monkeypatch):
    monkeypatch.setenv("ASSEMBLYAI_API_KEYS", "aai-platform")
    used = []

    async def connect(url, **kwargs):
        used.append(kwargs["additional_headers"]["Authorization"])
        raise _Refused(401)

    provider = await _assemblyai(monkeypatch, "tenant-own", connect)
    with pytest.raises(RuntimeError, match="authentication failed"):
        await provider.pre_connect("call")
    assert used == ["tenant-own"]
    assert provider._guard.in_flight == 0
