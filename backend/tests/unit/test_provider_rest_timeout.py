"""
Regression test for P1-7 / P1-8: Twilio/Vonage REST calls had no timeout.

A stalled provider TCP connection used to hang the awaiting coroutine
indefinitely (bare ``loop.run_in_executor`` with no bound), wedging
origination/teardown for the whole call. Both adapters now bound every
REST round-trip with ``asyncio.wait_for(..., timeout=_REST_TIMEOUT_SECONDS)``
and raise a clean ``TimeoutError`` instead of hanging.

These tests simulate a hung provider call (a blocking function that sleeps
far longer than the timeout budget) and assert the adapter raises
``TimeoutError`` within that budget rather than hanging forever.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from app.infrastructure.telephony import twilio_provider_adapter as twilio_mod
from app.infrastructure.telephony import vonage_provider_adapter as vonage_mod
from app.infrastructure.telephony.twilio_provider_adapter import TwilioProviderAdapter
from app.infrastructure.telephony.vonage_provider_adapter import VonageProviderAdapter

# Keep the test fast: shrink the module's timeout budget instead of actually
# waiting out the production default (10s).
_TEST_TIMEOUT = 0.2
# The simulated hung provider call blocks far longer than the timeout so a
# regression (no timeout at all) would make the test hang/timeout-fail loudly
# rather than silently pass.
_HANG_SECONDS = 5.0


class _FakeTwilioCallResource:
    def update(self, **kwargs):
        time.sleep(_HANG_SECONDS)


class _FakeTwilioClient:
    def calls(self, call_id):
        return _FakeTwilioCallResource()


class _FakeVonageVoice:
    def update_call(self, call_id, payload):
        time.sleep(_HANG_SECONDS)


class _FakeVonageClient:
    def __init__(self):
        self.voice = _FakeVonageVoice()


@pytest.mark.asyncio
async def test_twilio_hangup_times_out_instead_of_hanging(monkeypatch):
    monkeypatch.setattr(twilio_mod, "_REST_TIMEOUT_SECONDS", _TEST_TIMEOUT)

    adapter = TwilioProviderAdapter(account_sid="ACxxx", auth_token="secret")
    monkeypatch.setattr(adapter, "_get_client", lambda: _FakeTwilioClient())

    start = time.perf_counter()
    with pytest.raises(TimeoutError):
        await adapter.hangup("CAxxxxxxxxxxxx")
    elapsed = time.perf_counter() - start

    # Must return well within the hang window, proving the coroutine was
    # unblocked by the timeout guard rather than waiting out the fake hang.
    assert elapsed < _HANG_SECONDS


@pytest.mark.asyncio
async def test_vonage_hangup_times_out_instead_of_hanging(monkeypatch):
    monkeypatch.setattr(vonage_mod, "_REST_TIMEOUT_SECONDS", _TEST_TIMEOUT)

    adapter = VonageProviderAdapter(
        api_key="key", api_secret="secret", app_id="app-id", private_key="pem-body"
    )
    monkeypatch.setattr(adapter, "_get_client", lambda: _FakeVonageClient())

    start = time.perf_counter()
    with pytest.raises(TimeoutError):
        await adapter.hangup("conv-uuid")
    elapsed = time.perf_counter() - start

    assert elapsed < _HANG_SECONDS
