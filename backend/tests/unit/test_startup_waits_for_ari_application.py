"""Startup recovery waits (bounded) until the ARI application is registered.

Bug hunt 2026-10-10 (outbound-telephony-dialer-12): AsteriskAdapter.connect()
only starts the event WebSocket; the Stasis application appears in ARI after
that socket registers it. main.py ran recover_orphaned_calls() immediately, so
GET /applications/<app> answered 404, the inverse ARI inventory returned None
and media reconciliation was deferred: every restart logged
'ari_application_channel_inventory_failed ... 404' and channels left by a
crashed predecessor stayed up until the next watchdog pass.
"""
from __future__ import annotations

import pytest

from app.infrastructure.telephony.asterisk_adapter import AsteriskAdapter


def _adapter(answers):
    ad = AsteriskAdapter()  # __init__ only reads env with defaults
    ad._session = object()
    calls = []

    async def ari(method, path, *_args, **_kwargs):
        calls.append((method, path))
        answer = answers.pop(0) if answers else answers_default
        if isinstance(answer, Exception):
            raise answer
        return answer

    answers_default = RuntimeError("ARI GET /applications/talky_ai -> 404")
    ad._ari = ari
    return ad, calls


@pytest.mark.asyncio
async def test_it_returns_once_the_application_is_registered():
    ad, calls = _adapter([RuntimeError("404"), RuntimeError("404"), {"name": "talky_ai", "channel_ids": []}])
    assert await ad.wait_for_application_registered(timeout_s=5) is True
    assert calls == [("GET", "/applications/talky_ai")] * 3


@pytest.mark.asyncio
async def test_it_gives_up_after_the_bound_instead_of_blocking_startup():
    ad, calls = _adapter([])
    assert await ad.wait_for_application_registered(timeout_s=0.6) is False
    assert 1 <= len(calls) <= 6
