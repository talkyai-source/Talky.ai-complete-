"""Offline HTTP/SSE through real Groq serialization and tool continuation only."""
import copy
from dataclasses import replace
import json
import os
import socket
import subprocess
import sys
import threading
from types import SimpleNamespace

import httpx
import pytest

from scripts import qualify_ag02_semantic as q


@pytest.fixture(autouse=True)
async def no_network(monkeypatch):
    def denied(*_args, **_kwargs):
        raise AssertionError("This suite cannot contact a provider or database")
    async def denied_async(*_args, **_kwargs):
        return denied()
    import asyncio
    original_connect, original_ex, original_pair = socket.socket.connect, socket.socket.connect_ex, socket.socketpair
    local = threading.local()
    def connect(sock, address, original):
        if getattr(local, "pair", False) and address[0] in {"127.0.0.1", "::1"}:
            return original(sock, address)
        return denied()
    def pair(*args, **kwargs):
        local.pair = True
        try:
            return original_pair(*args, **kwargs)
        finally:
            local.pair = False
    monkeypatch.setattr(socket.socket, "connect", lambda sock, address: connect(sock, address, original_connect))
    monkeypatch.setattr(socket.socket, "connect_ex", lambda sock, address: connect(sock, address, original_ex))
    monkeypatch.setattr(socket, "socketpair", pair)
    monkeypatch.setattr(socket, "getaddrinfo", denied)
    monkeypatch.setattr(asyncio.BaseEventLoop, "create_connection", denied_async)


@pytest.fixture
def rt():
    return q.runtime()


@pytest.fixture
def profile(rt):
    value = q.Profile(model="openai/gpt-oss-20b", endpoint=q.ENDPOINT, knowledge_mode="retrieve")
    q.validate_profile(value, rt)
    return value


def case():
    return {"id": "offline-only", "suite": "holdout", "query": "When do I get my purchase money back?",
            "expected_node_ids": ["refund"], "human_review_rule": "REVIEW_ONLY_MARKER",
            "nodes": [{"id": "refund", "heading": "Refund", "content": "Refund processing takes five working days after approval.",
                       "source_id": "synthetic", "source_version": 1, "version": 1, "depth": 1, "summary": ""}]}


def sse(*, tool=False, text="", finish=None):
    delta = {"tool_calls": [{"index": 0, "id": "lookup-1", "type": "function", "function": {
        "name": "lookup_company_knowledge", "arguments": '{"query":"refund processing"}'}}]} if tool else {"content": text}
    event = {"id": "synthetic-response", "object": "chat.completion.chunk", "created": 1,
             "model": "openai/gpt-oss-20b", "choices": [{"index": 0, "delta": delta,
             "finish_reason": finish or ("tool_calls" if tool else "stop")}],
             "x_groq": {"usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}}}
    class SSEStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield ("data: " + json.dumps(event) + "\n\ndata: [DONE]\n\n").encode()
    return httpx.Response(200, headers={"content-type": "text/event-stream", "x-request-id": "synthetic-id"},
                          stream=SSEStream())


def test_predeclared_cases_and_labels_never_enter_retrieve_inputs(profile, rt):
    gold, cases = q.load_cases()
    assert len(cases) == 80 and sum(c["answerable"] for c in gold["cases"]) == 45
    assert len({c["id"] for c in cases}) == 80
    for original in cases:
        candidate = copy.deepcopy(original)
        candidate.update(expected_node_ids=["DO_NOT_SEND_EXPECTATIONS"], required_source_fragments=["DO_NOT_SEND_EXPECTATIONS"],
                         human_review_rule="DO_NOT_SEND_EXPECTATIONS")
        prompt, messages = q.model_inputs(candidate, profile, rt)
        payload = json.dumps([prompt, [m.content for m in messages]])
        assert "DO_NOT_SEND_EXPECTATIONS" not in payload
        assert messages[-1].content == original["query"]
        assert prompt == rt.addendum()


def test_map_profile_requires_separate_review(profile, rt):
    with pytest.raises(q.QualificationBoundaryError):
        q.validate_profile(replace(profile, knowledge_mode="map_retrieve"), rt)


@pytest.mark.parametrize("field,value", [("endpoint", "https://example.invalid/chat/completions"),
    ("model", "unlisted-model"), ("max_requests", 161), ("max_requests", 0),
    ("max_total_completion_tokens", 204801), ("temperature", 0.8), ("max_tokens", 512)])
def test_profile_rejects_unreviewed_destinations_settings_or_budget(profile, rt, field, value):
    with pytest.raises(q.QualificationBoundaryError):
        q.validate_profile(replace(profile, **{field: value}), rt)


def test_unexpected_reasoning_reserve_requires_replan(profile, rt):
    with pytest.raises(q.QualificationBoundaryError):
        q.validate_profile(profile, SimpleNamespace(**{**vars(rt), "reserve": 2048}))


def test_dry_run_does_not_initialize_provider_or_read_key(tmp_path, monkeypatch, rt):
    monkeypatch.setattr(q, "runtime", lambda: rt)
    monkeypatch.setattr(q, "forbid_dotenv_reads", lambda: None)  # Global audit hook tested in child below.
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Provider construction forbidden in dry run")
    monkeypatch.setattr(rt.Provider, "__init__", forbidden)
    original_get = os.environ.get
    monkeypatch.setattr(os.environ, "get", lambda key, *args: forbidden() if key == q.KEY_ENV else original_get(key, *args))
    output = tmp_path / "plan.json"
    assert q.main(["--endpoint", q.ENDPOINT, "--model", "openai/gpt-oss-20b", "--knowledge-mode", "retrieve",
                   "--output", str(output)]) == 0
    report = json.loads(output.read_text())
    assert report["status"] == "dry_run_no_provider_requests" and report["case_count"] == 80
    assert report["semantic_quality_pass"] is None and report["effective_completion_ceiling"] == 1280


def test_existing_output_cannot_be_overwritten(tmp_path, monkeypatch, rt):
    monkeypatch.setattr(q, "runtime", lambda: rt)
    monkeypatch.setattr(q, "forbid_dotenv_reads", lambda: None)
    output = tmp_path / "existing.json"
    output.write_text("original")
    with pytest.raises(FileExistsError):
        q.main(["--endpoint", q.ENDPOINT, "--model", "openai/gpt-oss-20b", "--knowledge-mode", "retrieve", "--output", str(output)])
    assert output.read_text() == "original"


def test_dotenv_file_read_is_denied_in_cli_process(tmp_path):
    synthetic = tmp_path / ".env"
    synthetic.write_text("SYNTHETIC_MARKER_ONLY=unused")
    code = ("from pathlib import Path; from scripts.qualify_ag02_semantic import forbid_dotenv_reads; "
            "forbid_dotenv_reads(); Path(__import__('sys').argv[1]).read_text()")
    result = subprocess.run([sys.executable, "-B", "-c", code, str(synthetic)],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode != 0 and "Environment-file access is not permitted" in result.stderr
    assert "SYNTHETIC_MARKER_ONLY" not in result.stderr


async def test_real_adapter_two_rounds_capture_original_query_evidence_answer_and_profile(profile, rt):
    calls = []
    def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        return sse(tool=True) if len(calls) == 1 else sse(text="An approved refund takes five working days.")
    transport = q.BoundedTransport(httpx.MockTransport(handler), profile)
    provider = await q.build_provider(rt, profile, transport, "synthetic-not-a-real-key")
    try:
        result = await q.run_case(provider, transport, profile, rt, case())
    finally:
        await provider.cleanup()
    assert result["completed"] and len(calls) == 2, (result, transport.denials,
        [{key: value for key, value in r.items() if key != "request"} for r in transport.requests])
    assert all(p["max_completion_tokens"] == 1280 and p["reasoning_effort"] == "low" and
               p["include_reasoning"] is False and p["temperature"] == 0.4 for p in calls)
    assert "tools" in calls[0] and "tools" not in calls[1]
    assert case()["query"] in calls[0]["messages"][-1]["content"]
    assert calls[1]["messages"][-1]["role"] == "tool"
    assert result["lookups"][0]["query"] == "refund processing"
    assert result["lookups"][0]["evidence"]["passages"][0]["source_id"] == "synthetic"
    assert result["model_final_output"] == "An approved refund takes five working days."
    assert result["mechanical"]["matched_expected_passage"] and result["semantic_quality_pass"] is None
    assert result["human_answer_fidelity"] == "pending"
    assert transport.reserved_completion == 2560 and len(transport.requests[0]["usage_events"]) == 1
    assert "synthetic-not-a-real-key" not in json.dumps(transport.requests)


async def test_wrong_final_answer_cannot_be_counted_as_semantic_success(profile, rt):
    calls = []
    def handler(request):
        calls.append(request)
        return sse(tool=True) if len(calls) == 1 else sse(text="Refunds arrive instantly without approval.")
    transport = q.BoundedTransport(httpx.MockTransport(handler), profile)
    provider = await q.build_provider(rt, profile, transport, "synthetic-not-a-real-key")
    try:
        result = await q.run_case(provider, transport, profile, rt, case())
    finally:
        await provider.cleanup()
    assert result["mechanical"]["matched_expected_passage"] is True
    assert result["semantic_quality_pass"] is None and result["human_answer_fidelity"] == "pending"
    assert "instantly" in result["model_final_output"]


async def test_explicit_designated_key_wins_over_ambient_pool(profile, rt, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEYS", "ambient-synthetic-a,ambient-synthetic-b")
    transport = q.BoundedTransport(httpx.MockTransport(lambda _r: sse(text="ok")), profile)
    provider = await q.build_provider(rt, profile, transport, "designated-synthetic")
    try:
        assert provider._pool is None and set(provider._clients_by_key) == {"designated-synthetic"}
        with pytest.raises(q.QualificationBoundaryError, match="designated credential"):
            provider._client_for("ambient-synthetic-a")
    finally:
        await provider.cleanup()


async def test_provider_truncation_stays_failed_with_original_output(profile, rt):
    transport = q.BoundedTransport(httpx.MockTransport(lambda _request: sse(text="Partial", finish="length")), profile)
    provider = await q.build_provider(rt, profile, transport, "synthetic-not-a-real-key")
    try:
        result = await q.run_case(provider, transport, profile, rt, case())
    finally:
        await provider.cleanup()
    assert not result["completed"] and result["error_type"] == "LLMStreamStalled"
    assert len(transport.requests) == 1 and result["semantic_quality_pass"] is None


def request_payload():
    return {"model": "openai/gpt-oss-20b", "stream": True, "max_completion_tokens": 1280,
            "temperature": 0.4, "reasoning_effort": "low", "include_reasoning": False,
            "messages": [{"role": "user", "content": case()["query"]}]}


@pytest.mark.parametrize("mutation,reason", [
    ({"url": "https://api.groq.com/other"}, "destination_or_method"),
    ({"method": "GET"}, "destination_or_method"),
    ({"max_completion_tokens": 1281}, "completion_per_request"),
    ({"model": "other"}, "model_or_stream"),
    ({"temperature": 0.8}, "effective_profile"),
    ({"reasoning_effort": "high"}, "effective_profile"),
    ({"include_reasoning": True}, "effective_profile"),
    ({"max_completion_tokens": 1200}, "effective_completion_ceiling"),
    ({"messages": [{"role": "user", "content": "Wrong original question"}]}, "original_question_missing"),
    ({"tools": [{"function": {"name": "send_email"}}]}, "unoffered_action_tool"),
])
async def test_transport_refuses_unreviewed_wire_request_before_dispatch(profile, mutation, reason):
    def forbidden(_request):
        raise AssertionError("Rejected request must never dispatch")
    transport = q.BoundedTransport(httpx.MockTransport(forbidden), profile)
    transport.case = case()
    changes = dict(mutation)
    method, url = changes.pop("method", "POST"), changes.pop("url", q.ENDPOINT)
    request = httpx.Request(method, url, json={**request_payload(), **changes})
    with pytest.raises(q.QualificationBoundaryError, match=reason):
        await transport.handle_async_request(request)
    assert transport.requests == []


@pytest.mark.parametrize("limit", ["max_requests", "max_total_completion_tokens", "max_input_bytes"])
async def test_retry_and_wire_input_budgets_apply_before_dispatch(profile, limit):
    overrides = {"max_requests": 1, "max_total_completion_tokens": 1280, "max_input_bytes": 1}
    p = replace(profile, **{limit: overrides[limit]})
    seen = []
    transport = q.BoundedTransport(httpx.MockTransport(lambda request: seen.append(request) or sse(text="ok")), p)
    transport.case = case()
    request = httpx.Request("POST", q.ENDPOINT, json=request_payload())
    if limit != "max_input_bytes":
        response = await transport.handle_async_request(request)
        await response.aclose()
    with pytest.raises(q.QualificationBoundaryError):
        await transport.handle_async_request(request)
    assert len(seen) == (0 if limit == "max_input_bytes" else 1)
