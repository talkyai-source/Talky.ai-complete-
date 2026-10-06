"""Historical query-profile data boundaries and retirement of its CLI.

These checks preserve the fixed corpus, label isolation and bounded profile
contract. They do not execute or qualify current model-selected sections.
Real provider tool continuation is covered by the shared adapter test modules.
"""
import copy
from dataclasses import replace
import json
import os
import socket
import subprocess
import sys
import threading
from types import SimpleNamespace

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


@pytest.mark.parametrize("execute", [False, True])
def test_retired_cli_refuses_before_runtime_credentials_or_output(tmp_path, monkeypatch, execute):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Retired profile must stop before runtime or provider setup")
    monkeypatch.setattr(q, "runtime", forbidden)
    monkeypatch.setattr(q, "execute", forbidden)
    monkeypatch.setattr(q, "_legacy_main", forbidden)
    original_get = os.environ.get
    monkeypatch.setattr(os.environ, "get", lambda key, *args: forbidden() if key == q.KEY_ENV else original_get(key, *args))
    output = tmp_path / "plan.json"
    args = ["--endpoint", q.ENDPOINT, "--model", "openai/gpt-oss-20b", "--knowledge-mode", "retrieve",
            "--output", str(output)] + (["--execute-provider"] if execute else [])
    with pytest.raises(q.QualificationBoundaryError, match="superseded.*section-selection"):
        q.main(args)
    assert not output.exists()


def test_retired_cli_leaves_existing_evidence_untouched(tmp_path):
    output = tmp_path / "existing.json"
    output.write_text("original")
    with pytest.raises(q.QualificationBoundaryError, match="superseded"):
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
