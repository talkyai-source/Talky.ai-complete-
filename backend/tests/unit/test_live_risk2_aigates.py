"""Live-risk audit 2026-10-20, follow-up: aiopt-1/6 gates on clone + benchmark,
aiopt-4 DeepSeek offered when only the DEEPSEEK_API_KEYS pool is set."""
from __future__ import annotations

import importlib

import pytest


def _route(router, path, method):
    for r in router.routes:
        if getattr(r, "path", None) == path and method in r.methods:
            return r
    raise AssertionError(f"no route {method} {path}")


def _names(route):
    return [getattr(d.call, "__qualname__", repr(d.call)) for d in route.dependant.dependencies]


@pytest.mark.parametrize("module,path,method", [
    ("clone", "/voices/clone", "POST"),
    ("clone", "/voices/cloned/{clone_id}", "DELETE"),
    ("benchmark", "/benchmark", "POST"),
])
def test_spend_and_clone_routes_require_tenants_update(module, path, method):
    mod = importlib.import_module(f"app.api.v1.endpoints.ai_options.{module}")
    assert any("permission_checker" in n for n in _names(_route(mod.router, path, method)))


@pytest.mark.parametrize("module,path,method", [
    ("clone", "/voices/clone", "POST"),
    ("benchmark", "/benchmark", "POST"),
])
def test_clone_create_and_benchmark_are_rate_limited(module, path, method):
    mod = importlib.import_module(f"app.api.v1.endpoints.ai_options.{module}")
    assert any("rate_limit" in n for n in _names(_route(mod.router, path, method)))


@pytest.mark.asyncio
async def test_deepseek_offered_with_only_the_key_pool(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEYS", "k1,k2")
    from app.api.v1.endpoints.ai_options.providers import list_providers

    response = await list_providers()

    assert "deepseek" in response.llm["providers"]
