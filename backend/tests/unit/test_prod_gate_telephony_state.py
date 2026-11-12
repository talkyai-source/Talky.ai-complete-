"""HA plan, phase 3: production refuses a call process on in-memory phone state.

With TELEPHONY_STATE_BACKEND=memory the owner-lock acquire always returns True,
so every call process believes it owns the phones. Two of them (a restart race,
a stray second worker, or the second APP server) would both drive Asterisk.
The dashboard process (TELEPHONY_ENABLED=false) never touches the phones.
"""
from __future__ import annotations

import pytest

from app.core import prod_gate


def _rules(violations):
    return {v.rule for v in violations}


@pytest.fixture(autouse=True)
def _call_process(monkeypatch):
    monkeypatch.delenv("TELEPHONY_ENABLED", raising=False)
    monkeypatch.delenv("TELEPHONY_STATE_BACKEND", raising=False)


def test_unset_backend_means_memory_and_is_refused():
    assert _rules(prod_gate._check_telephony_state_backend()) == {"telephony_state_not_shared"}


@pytest.mark.parametrize("value", ["memory", "MEMORY", "redis-cluster", " "])
def test_anything_but_redis_is_refused(monkeypatch, value):
    monkeypatch.setenv("TELEPHONY_STATE_BACKEND", value)

    assert _rules(prod_gate._check_telephony_state_backend()) == {"telephony_state_not_shared"}


@pytest.mark.parametrize("value", ["redis", " Redis "])
def test_redis_passes(monkeypatch, value):
    monkeypatch.setenv("TELEPHONY_STATE_BACKEND", value)

    assert prod_gate._check_telephony_state_backend() == []


def test_the_dashboard_process_is_not_checked(monkeypatch):
    monkeypatch.setenv("TELEPHONY_ENABLED", "false")
    monkeypatch.setenv("TELEPHONY_STATE_BACKEND", "memory")

    assert prod_gate._check_telephony_state_backend() == []


def test_the_production_gate_refuses_to_boot_on_memory(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("TELEPHONY_STATE_BACKEND", "memory")
    # Every other rule passes, so the refusal can only come from this one.
    for name in dir(prod_gate):
        if name.startswith("_check_") and name != "_check_telephony_state_backend":
            monkeypatch.setattr(prod_gate, name, lambda: [])

    with pytest.raises(prod_gate.ProductionGateError) as refused:
        prod_gate.enforce_production_gate()

    assert "[telephony_state_not_shared]" in str(refused.value)
