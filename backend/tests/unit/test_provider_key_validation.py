"""F3-06: bounded fake-only provider credential probes, never billable calls."""
from __future__ import annotations

import asyncio
import importlib

import httpx
import pytest


KEY = "synthetic-private-provider-key-1234"
CASES = [
    ("groq", "https://api.groq.com/openai/v1/models", "authorization", "Bearer " + KEY, {"data": []}),
    ("cerebras", "https://api.cerebras.ai/v1/models", "authorization", "Bearer " + KEY, {"data": []}),
    ("openai", "https://api.openai.com/v1/models", "authorization", "Bearer " + KEY, {"data": []}),
    ("deepseek", "https://api.deepseek.com/models", "authorization", "Bearer " + KEY, {"data": []}),
    ("xai", "https://api.x.ai/v1/models", "authorization", "Bearer " + KEY, {"data": []}),
    ("gemini", "https://generativelanguage.googleapis.com/v1beta/models", "x-goog-api-key", KEY, {"models": []}),
    ("anthropic", "https://api.anthropic.com/v1/models", "x-api-key", KEY, {"data": []}),
    ("deepgram", "https://api.deepgram.com/v1/models", "authorization", "Token " + KEY, {"stt": [], "tts": []}),
    ("cartesia", "https://api.cartesia.ai/models", "authorization", "Bearer " + KEY, {"data": []}),
    ("elevenlabs", "https://api.elevenlabs.io/v1/user", "xi-api-key", KEY, {"user_id": "synthetic-user"}),
    ("assemblyai", "https://api.assemblyai.com/v2/transcript", "authorization", KEY, {"transcripts": []}),
]


@pytest.fixture
def probe():
    return importlib.import_module("app.domain.services.provider_key_validation")


@pytest.mark.parametrize(("provider", "url", "header", "value", "body"), CASES)
async def test_probe_uses_fixed_authenticated_read_only_endpoint(probe, provider, url, header, value, body, caplog):
    requests = []

    def respond(request):
        requests.append(request)
        assert request.method == "GET"
        assert str(request.url).split("?")[0] == url
        assert request.headers[header] == value
        assert KEY not in str(request.url)
        if provider == "assemblyai":
            assert request.url.params["limit"] == "1"
        if provider == "anthropic":
            assert request.headers["anthropic-version"] == "2023-06-01"
        if provider == "cartesia":
            assert request.headers["cartesia-version"] == "2026-08-14"
        assert all(0 < seconds <= 5 for seconds in request.extensions["timeout"].values())
        return httpx.Response(200, json=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await probe.probe_provider_key(provider, KEY, client=client)
    assert result.status == "valid"
    assert result.checked_at.tzinfo is not None
    assert len(requests) == 1, "credential validation needs one bounded read, not catalog pagination"
    assert KEY not in repr(result) + caplog.text


@pytest.mark.parametrize("status", [401, 403])
async def test_provider_auth_rejection_is_invalid_without_echoing_body(probe, status, caplog):
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(status, text="private provider echo " + KEY)
    )) as client:
        result = await probe.probe_provider_key("groq", KEY, client=client)
    assert result.status == "invalid"
    assert KEY not in repr(result) + caplog.text


@pytest.mark.parametrize("status", [301, 302, 404, 408, 429, 500, 503])
async def test_transient_or_unrecognized_response_is_unknown_not_invalid(probe, status):
    seen = []

    def respond(request):
        seen.append(str(request.url))
        return httpx.Response(status, headers={"location": "https://untrusted.example.invalid/steal"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond), follow_redirects=True) as client:
        with pytest.raises(probe.ProviderProbeUnavailable) as caught:
            await probe.probe_provider_key("groq", KEY, client=client)
    assert len(seen) == 1
    assert KEY not in str(caught.value)


@pytest.mark.parametrize("body", [b"not-json", b"[]", b"{}", b'{"data":"not-a-list"}'])
async def test_malformed_success_is_not_credential_validation(probe, body):
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=body)
    )) as client:
        with pytest.raises(probe.ProviderProbeUnavailable):
            await probe.probe_provider_key("groq", KEY, client=client)


class EndlessBody(httpx.AsyncByteStream):
    def __init__(self):
        self.reads = 0
        self.closed = False

    async def __aiter__(self):
        for _ in range(100):
            self.reads += 1
            yield b"x" * 65536

    async def aclose(self):
        self.closed = True


async def test_stream_size_is_bounded_before_json_allocation(probe):
    stream = EndlessBody()
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, stream=stream)
    )) as client:
        with pytest.raises(probe.ProviderProbeUnavailable):
            await probe.probe_provider_key("groq", KEY, client=client)
    assert stream.reads <= 17, "at most 1 MiB plus the overflow chunk may be read"
    assert stream.closed


async def test_outer_deadline_bounds_a_stalled_probe(probe, monkeypatch):
    monkeypatch.setattr(probe, "PROBE_TIMEOUT_SECONDS", 0.02)

    async def stalled(request):
        await asyncio.Event().wait()

    async with httpx.AsyncClient(transport=httpx.MockTransport(stalled)) as client:
        with pytest.raises(probe.ProviderProbeUnavailable):
            await asyncio.wait_for(probe.probe_provider_key("groq", KEY, client=client), timeout=0.5)


async def test_cancellation_propagates_without_becoming_invalid(probe):
    async def cancelled(request):
        raise asyncio.CancelledError()

    async with httpx.AsyncClient(transport=httpx.MockTransport(cancelled)) as client:
        with pytest.raises(asyncio.CancelledError):
            await probe.probe_provider_key("groq", KEY, client=client)


@pytest.mark.parametrize("provider", ["unknown", "https://untrusted.example.invalid", "groq\nAuthorization:x"])
async def test_unknown_provider_never_sends_key_to_an_arbitrary_url(probe, provider):
    def forbidden(request):
        raise AssertionError("unsupported provider attempted network I/O")

    async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as client:
        with pytest.raises(ValueError):
            await probe.probe_provider_key(provider, KEY, client=client)


