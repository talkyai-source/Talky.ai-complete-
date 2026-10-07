"""Qualification uses current runtime/tool contracts and cannot award acceptance."""
import argparse
import json

import httpx
import pytest

from scripts import qualify_agent_sections as q


def args(tmp_path, **overrides):
    values = dict(provider="gemini", model="gemini-3.8-flash", temperature=0.4, max_tokens=256,
        max_requests=160, token_budget=204800, case=["H04"], credential_env=None,
        execute_provider=False, output=tmp_path / "result.json")
    return argparse.Namespace(**(values | overrides))


async def test_dry_plan_uses_actual_current_streamer_and_hides_grading_labels(tmp_path, monkeypatch):
    monkeypatch.setattr(q, "provider_for", lambda *_: pytest.fail("Dry plan initialized provider"))
    report = await q.run(args(tmp_path))
    assert report["error"] is None and report["production_approved"] is False
    assert report["case_ids"] == ["H04"]
    request = report["prepared_requests"][0]
    assert request["messages"][-1]["content"] == "Not Pulse, I mean Comet. Can I use it without a wall socket?"
    assert "source" in request["system_prompt"]
    wire = json.dumps(request)
    assert "human_review_rule" not in wire and "expected_node_ids" not in wire
    assert request["tools"][0]["function"]["name"] == "lookup_company_knowledge"
    assert report["cases"][0]["human_approval"] is None


async def test_full_original_question_set_prepares_without_scope_rewriting(tmp_path):
    report = await q.run(args(tmp_path, case=None))
    assert report["error"] is None and len(report["cases"]) == 80
    assert len(report["prepared_requests"]) == 80 and report["source_unchanged"]
    assert all(row["human_approval"] is None for row in report["cases"])


async def test_provider_execution_requires_designated_key_without_ambient_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "ambient-not-designated")
    monkeypatch.setattr(q, "provider_for", lambda *_: pytest.fail("Undesignated provider initialized"))
    report = await q.run(args(tmp_path, execute_provider=True))
    assert report["error"] == "ValueError" and report["cases"] == []
    assert "ambient-not-designated" not in (tmp_path / "result.json").read_text()


@pytest.mark.parametrize("change", [{"case": ["missing"]}, {"model": "unknown"}, {"max_requests": 0}])
async def test_invalid_plans_fail_before_output_or_provider(tmp_path, change):
    with pytest.raises(ValueError):
        await q.run(args(tmp_path, **change))
    assert not (tmp_path / "result.json").exists()


async def test_existing_evidence_is_not_overwritten(tmp_path):
    path = tmp_path / "result.json"
    path.write_text("previous evidence")
    with pytest.raises(ValueError):
        await q.run(args(tmp_path))
    assert path.read_text() == "previous evidence"


def transport(provider="groq", model="openai/gpt-oss-20b", **kwargs):
    calls = []
    async def serve(request):
        calls.append(request)
        return httpx.Response(200, text='data: {"usage":{"completion_tokens":2}}\n\ndata: [DONE]\n\n')
    guard = q.BoundedTransport(provider, model, max_requests=kwargs.get("max_requests", 1),
        token_budget=kwargs.get("token_budget", 1280), inner=httpx.MockTransport(serve))
    return guard, calls


async def test_transport_binds_endpoint_model_and_actual_retry_budget_without_headers():
    guard, calls = transport()
    async with httpx.AsyncClient(transport=guard, trust_env=False) as client:
        body = {"model": "openai/gpt-oss-20b", "max_completion_tokens": 1280, "messages": []}
        first = await client.post("https://api.groq.com/openai/v1/chat/completions", json=body,
            headers={"Authorization": "Bearer synthetic-secret"})
        assert first.status_code == 200
        with pytest.raises(RuntimeError, match="budget"):
            await client.post("https://api.groq.com/openai/v1/chat/completions", json=body)
    assert len(calls) == 1 and guard.reserved_tokens == 1280 and guard.refusals == 1
    assert "synthetic-secret" not in json.dumps(guard.records)
    assert "completion_tokens" in guard.records[0]["response_stream"]


@pytest.mark.parametrize("url,body", [
    ("https://other.invalid/openai/v1/chat/completions", {"model": "openai/gpt-oss-20b", "max_tokens": 10}),
    ("https://api.groq.com/openai/v1/models", {"model": "openai/gpt-oss-20b", "max_tokens": 10}),
    ("https://api.groq.com/openai/v1/chat/completions?api_key=secret", {"model": "openai/gpt-oss-20b", "max_tokens": 10}),
    ("https://api.groq.com/openai/v1/chat/completions", {"model": "different", "max_tokens": 10}),
    ("https://api.groq.com/openai/v1/chat/completions", {"model": "openai/gpt-oss-20b"}),
])
async def test_transport_rejects_endpoint_or_profile_escape(url, body):
    guard, calls = transport()
    async with httpx.AsyncClient(transport=guard, trust_env=False) as client:
        with pytest.raises(AssertionError):
            await client.post(url, json=body)
    assert calls == []


async def test_gemini_actual_wire_shape_reserves_shared_thinking_ceiling():
    guard, calls = transport("gemini", "gemini-3.8-flash")
    async with httpx.AsyncClient(transport=guard, trust_env=False) as client:
        await client.post("https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:streamGenerateContent?alt=sse",
            json={"generationConfig": {"maxOutputTokens": 1280}, "contents": []})
    assert len(calls) == 1 and guard.reserved_tokens == 1280


async def test_effective_profile_drift_is_refused_before_dispatch():
    guard, calls = transport()
    guard.expected_profile = {"temperature": 0.4, "output_ceiling": 1280}
    async with httpx.AsyncClient(transport=guard, trust_env=False) as client:
        with pytest.raises(AssertionError):
            await client.post("https://api.groq.com/openai/v1/chat/completions", json={
                "model": "openai/gpt-oss-20b", "max_completion_tokens": 1200, "temperature": 0.4})
    assert calls == []


async def test_sdk_early_stop_still_records_consumed_sse_on_close():
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: first\n\n'
            yield b'data: [DONE]\n\n'
            raise AssertionError("SDK must not consume beyond its terminal marker")
    record = {}
    wrapped = q.CapturedStream(Stream(), record)
    iterator = wrapped.__aiter__()
    assert await anext(iterator) == b'data: first\n\n'
    assert await anext(iterator) == b'data: [DONE]\n\n'
    await wrapped.aclose()
    assert record["response_stream"] == 'data: first\n\ndata: [DONE]\n\n'
    await iterator.aclose()


async def test_failed_initialization_still_records_safe_error(tmp_path, monkeypatch):
    monkeypatch.setenv("DESIGNATED_SYNTHETIC_KEY", "synthetic-never-transmitted")
    async def failed(*_):
        raise RuntimeError("credential-bearing-provider-message")
    monkeypatch.setattr(q, "provider_for", failed)
    report = await q.run(args(tmp_path, execute_provider=True, credential_env="DESIGNATED_SYNTHETIC_KEY"))
    assert report["error"] == "RuntimeError" and report["requests"] == []
    assert "credential-bearing" not in (tmp_path / "result.json").read_text()


@pytest.mark.parametrize("provider,model", [
    ("gemini", "gemini-3.8-flash"), ("groq", "openai/gpt-oss-20b"), ("openai", "gpt-6-luna"),
])
async def test_actual_sdk_adapters_use_bounded_transport_and_cannot_approve_wrong_answer(tmp_path, provider, model):
    async def serve(request):
        if provider == "gemini":
            event = {"candidates": [{"content": {"role": "model", "parts": [{"text": "A deliberately wrong answer."}]}, "finishReason": "STOP"}]}
            body = "data: " + json.dumps(event) + "\n\n"
        else:
            event = {"id": "synthetic", "choices": [{"index": 0, "delta": {"content": "A deliberately wrong answer."}, "finish_reason": "stop"}]}
            body = "data: " + json.dumps(event) + "\n\ndata: [DONE]\n\n"
        return httpx.Response(200, text=body, headers={"Content-Type": "text/event-stream"})
    guard = q.BoundedTransport(provider, model, max_requests=2, token_budget=4096, inner=httpx.MockTransport(serve))
    options = args(tmp_path, provider=provider, model=model)
    adapter = await q.provider_for(options, guard, "synthetic-never-transmitted")
    try:
        result = await q.run_case(next(r for r in q.cases() if r["case"]["id"] == "H04"), options, adapter)
    finally:
        await adapter.cleanup()
        await guard.aclose()
    assert len(guard.records) == 1
    assert "deliberately wrong" in result["answer"]
    assert result["human_approval"] is None and result["semantic_review"] == "pending"
    assert "human_review_rule" not in json.dumps(guard.records[0]["body"])
