"""Loose ends of the 2026-10-20 live-risk fixes, found while integrating.

- auth-2: the Ask-AI widget socket, MFA and passkeys still trusted the
  leftmost X-Forwarded-For entry (attacker-controlled: nginx appends to it),
  so their per-IP limits could be dodged with a forged header.
- Removing a contact cancelled its dialer jobs by lead id alone; the helper
  now takes the tenant (the DB role bypasses RLS), so the caller passes it.
- The AI Options benchmark offered DeepSeek only from the single
  DEEPSEEK_API_KEY, not from the DEEPSEEK_API_KEYS pool.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from starlette.datastructures import Headers

FORGED = {"x-forwarded-for": "6.6.6.6, 203.0.113.9", "x-real-ip": "203.0.113.9"}


def _conn(peer: str, headers: dict):
    return SimpleNamespace(client=SimpleNamespace(host=peer), headers=Headers(headers))


def _readers():
    from app.api.v1.endpoints import ask_ai_ws, passkeys
    from app.api.v1.endpoints.mfa import _shared as mfa_shared

    return {
        "ask_ai_ws": ask_ai_ws._client_ip,
        "mfa": mfa_shared._get_client_ip,
        "passkeys": passkeys._get_client_ip,
    }


@pytest.mark.parametrize("name", ["ask_ai_ws", "mfa", "passkeys"])
def test_a_forged_leftmost_forwarded_for_does_not_change_the_client_ip(name):
    read = _readers()[name]
    # Through nginx (loopback peer): the address nginx saw, not the forged one.
    assert read(_conn("127.0.0.1", FORGED)) == "203.0.113.9"
    # Straight to the app (public peer): forwarded headers are not trusted.
    assert read(_conn("198.51.100.7", FORGED)) == "198.51.100.7"


@pytest.mark.asyncio
async def test_removing_a_contact_cancels_its_jobs_within_the_tenant(monkeypatch):
    from app.api.v1.endpoints import campaigns as campaigns_ep
    from app.domain.services.dialer import job_lifecycle
    from tests.unit.test_contact_direction_boundaries import USER, _DB

    seen = {}

    def fake_cancel(db_client, lead_id, **kwargs):
        seen.update(kwargs, lead_id=lead_id)
        return 0

    monkeypatch.setattr(job_lifecycle, "cancel_active_jobs_for_lead", fake_cancel)
    await campaigns_ep.remove_contact_from_campaign("c1", "lead1", USER, _DB())

    assert seen.get("lead_id") == "lead1"
    assert seen.get("tenant_id") == USER.tenant_id


def test_benchmark_offers_deepseek_from_the_key_pool(monkeypatch):
    from app.api.v1.endpoints.ai_options.benchmark import _select_benchmark_llm
    from app.domain.models.ai_config import AIProviderConfig
    from app.infrastructure.llm.deepseek import DeepSeekLLMProvider
    from app.infrastructure.providers import key_pool

    monkeypatch.setattr(key_pool, "_ROTATIONS", {})
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEYS", "sk-pool-a,sk-pool-b")

    llm, api_key = _select_benchmark_llm(AIProviderConfig(llm_provider="deepseek", llm_model="deepseek-flash"))

    assert isinstance(llm, DeepSeekLLMProvider)
    assert api_key in {"sk-pool-a", "sk-pool-b"}
