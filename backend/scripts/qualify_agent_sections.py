"""Prepare or run the existing voice streamer against synthetic knowledge cases.

Dry by default. Execution needs a designated credential environment variable.
This records text/tool evidence, never telephone, persistence or human approval.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import AsyncMock, MagicMock

import httpx

from app.domain.models.conversation import Message, MessageRole
from app.domain.models.session import CallSession
from app.domain.services.voice_pipeline_service import VoicePipelineService
from app.services.scripts.knowledge.sections import build_section_catalog
from app.services.scripts.prompts.composer import compose_prompt

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "backend/tests/fixtures/knowledge"
ENDPOINTS = {
    "groq": ("api.groq.com", "/openai/v1/chat/completions"),
    "openai": ("api.openai.com", "/v1/chat/completions"),
    "gemini": ("generativelanguage.googleapis.com", "/v1beta/models/{model}:streamGenerateContent"),
}


def cases():
    """Keep all original questions/rubrics; labels never enter model input."""
    gold = json.loads((FIXTURES / "ag02_gold.json").read_text(encoding="utf-8"))
    holdout = json.loads((FIXTURES / "ag02_semantic_holdouts.json").read_text(encoding="utf-8"))
    rows = [{"case": row, "nodes": gold["nodes"]} for row in gold["cases"]]
    rows += [{"case": row, "nodes": [
        {"source_id": "synthetic-holdout-" + row["corpus"], "source_version": 1, "version": 1, **node}
        for node in holdout["corpora"][row["corpus"]]]} for row in holdout["cases"]]
    return rows


class BoundedTransport(httpx.AsyncBaseTransport):
    """Count actual attempts (including retries), before any network dispatch."""

    def __init__(self, provider, model, *, max_requests, token_budget, inner=None):
        self.host, self.path = ENDPOINTS[provider]
        self.path = self.path.format(model=model)
        self.model = model
        self.max_requests, self.token_budget = max_requests, token_budget
        self.inner = inner or httpx.AsyncHTTPTransport(retries=0)
        self.records = []
        self.refusals = 0
        self.reserved_tokens = self.request_bytes = 0
        self.expected_profile = None

    async def handle_async_request(self, request):
        assert request.method == "POST" and request.url.scheme == "https"
        assert request.url.host == self.host and request.url.path == self.path
        assert request.url.port in (None, 443)
        assert all(key == "alt" and value == "sse" for key, value in request.url.params.multi_items())
        raw = await request.aread()
        body = json.loads(raw)
        assert body.get("model", self.model) == self.model
        config = body.get("generationConfig", body)
        ceiling = config.get("maxOutputTokens", config.get("max_completion_tokens", config.get("max_tokens")))
        assert type(ceiling) is int and ceiling > 0
        effective = {"temperature": config.get("temperature"), "output_ceiling": ceiling,
                     "reasoning_effort": config.get("reasoning_effort"),
                     "thinking": config.get("thinkingConfig")}
        if self.expected_profile is not None:
            assert effective["temperature"] == self.expected_profile["temperature"]
            assert ceiling == self.expected_profile["output_ceiling"]
        if (len(self.records) >= self.max_requests or self.reserved_tokens + ceiling > self.token_budget
                or len(raw) > 262144 or self.request_bytes + len(raw) > 16777216):
            self.refusals += 1
            raise RuntimeError("Qualification request budget exhausted")
        self.reserved_tokens += ceiling
        self.request_bytes += len(raw)
        record = {"body": body, "effective_profile": effective, "response_status": None, "response_stream": ""}
        self.records.append(record)
        response = await self.inner.handle_async_request(request)
        record["response_status"] = response.status_code
        if response.is_stream_consumed:
            record["response_stream"] = response.content.decode("utf-8", errors="replace")
        else:
            response.stream = CapturedStream(response.stream, record)
        return response

    async def aclose(self):
        await self.inner.aclose()


class CapturedStream(httpx.AsyncByteStream):
    def __init__(self, inner, record):
        self.inner, self.record = inner, record
        self.chunks = []

    async def __aiter__(self):
        size = 0
        try:
            async for chunk in self.inner:
                size += len(chunk)
                if size > 1048576:
                    raise RuntimeError("Qualification response exceeded capture limit")
                self.chunks.append(chunk)
                yield chunk
        finally:
            self.record["response_stream"] = b"".join(self.chunks).decode("utf-8", errors="replace")

    async def aclose(self):
        try:
            await self.inner.aclose()
        finally:
            self.record["response_stream"] = b"".join(self.chunks).decode("utf-8", errors="replace")


class DryProvider:
    supports_tools = True
    supports_streaming = True

    def __init__(self, provider, model, max_tokens):
        self.name, self._model, self._max_tokens = provider, model, max_tokens
        self.prepared = []

    async def stream_chat_with_tools(self, messages, **kwargs):
        self.prepared.append({
            "messages": [{"role": m.role.value, "content": m.content} for m in messages],
            **{key: kwargs.get(key) for key in ("system_prompt", "tools", "temperature", "max_tokens", "max_tool_rounds")},
        })
        yield "Prepared only."


async def provider_for(args, transport, key):
    config = {"api_key": key, "model": args.model, "temperature": args.temperature, "max_tokens": args.max_tokens}
    client = httpx.AsyncClient(transport=transport, trust_env=False, follow_redirects=False, timeout=20)
    if args.provider == "gemini":
        from google import genai
        from google.genai import types
        from app.infrastructure.llm.gemini import GeminiLLMProvider
        provider = GeminiLLMProvider()
        await provider.initialize(config)
        # Close this evaluation-owned construction before installing its guarded client.
        await provider._client.aio.aclose()
        provider._client.close()
        await provider.cleanup()
        provider._client = genai.Client(api_key=key, http_options=types.HttpOptions(
            api_version="v1beta", httpx_async_client=client, retry_options=types.HttpRetryOptions(attempts=1)))
        ceiling = provider._effective_max_output_tokens(args.model, provider._thinking_budget, args.max_tokens)
    elif args.provider == "groq":
        from groq import AsyncGroq
        from app.infrastructure.llm.groq import GroqLLMProvider
        provider = GroqLLMProvider()
        await provider.initialize(config)
        await provider.cleanup()
        provider._client = AsyncGroq(api_key=key, http_client=client, max_retries=0)
        provider._clients_by_key = {key: provider._client}
        from app.infrastructure.llm.groq import _THINKING_RESERVE_TOKENS
        ceiling = args.max_tokens + _THINKING_RESERVE_TOKENS
    else:
        from app.infrastructure.llm.openai import OpenAILLMProvider
        provider = OpenAILLMProvider()
        await provider.initialize(config)
        await provider.cleanup()
        provider._client = client
        client.base_url = "https://api.openai.com/v1/"
        client.headers["Authorization"] = f"Bearer {key}"
        ceiling = args.max_tokens
    transport.expected_profile = {"temperature": args.temperature, "output_ceiling": ceiling}
    return provider


async def run_case(row, args, provider):
    case = row["case"]
    prompt = compose_prompt("customer_support", "Alex", "Fictional demonstration company", {},
                            direction="inbound", knowledge_driven=True)
    history = []
    if case.get("previous_query"):
        history.append(Message(role=MessageRole.USER, content=case["previous_query"]))
    history.append(Message(role=MessageRole.USER, content=case["query"]))
    # The gold matrix already declares its synthetic scope. Do not rewrite its
    # node ownership or admit excluded controls into that snapshot.
    tenant = row["nodes"][0].get("tenant_id", "qualification")
    campaign = row["nodes"][0].get("campaign_id", "synthetic-source")
    session = CallSession(call_id="qualification-" + case["id"], tenant_id=tenant,
        campaign_id=campaign, lead_id="synthetic", provider_call_id="text-only", voice_id="synthetic",
        system_prompt=prompt, knowledge_mode="retrieve", conversation_history=history,
        llm_temperature=args.temperature, llm_max_tokens=args.max_tokens, llm_model=args.model)
    session._voice_action_context_loaded = True
    session._voice_action_capabilities = {}
    session._knowledge_catalog = build_section_catalog(row["nodes"], tenant_id=session.tenant_id,
        campaign_id=session.campaign_id, source_policy="call_snapshot")
    service = VoicePipelineService(stt_provider=AsyncMock(), llm_provider=provider,
        tts_provider=AsyncMock(), media_gateway=AsyncMock())
    service.latency_tracker = MagicMock()
    speech = []
    async def record_speech(_session, text, *_args, **_kwargs):
        speech.append(text)
        return False
    service.synthesize_and_send_audio = record_speech
    started = time.perf_counter()
    answer, _, _ = await asyncio.wait_for(service._stream_llm_and_tts(session), timeout=35)
    return {"id": case["id"], "original_question": case["query"], "review_only_rubric": case,
        "answer": answer, "submitted_text": speech, "seconds": round(time.perf_counter() - started, 3),
        "final_source_evidence": getattr(session, "_knowledge_evidence", None),
        "semantic_review": "pending", "human_approval": None}


async def run(args):
    selected = [row for row in cases() if not args.case or row["case"]["id"] in args.case]
    if args.case and set(args.case) != {row["case"]["id"] for row in selected}:
        raise ValueError("Unknown case ID; no partial selection was executed")
    if args.output.exists():
        raise ValueError("Output exists; preserve previous evidence")
    if not (0 < args.max_requests <= 640 and 0 < args.token_budget <= 819200 and 0 < args.max_tokens <= 1024):
        raise ValueError("Invalid finite request/token limits")
    if not 0 <= args.temperature <= 2:
        raise ValueError("Invalid temperature")
    from app.domain.models.ai_config import GEMINI_MODELS, GROQ_MODELS, OPENAI_MODELS
    menu = {"gemini": GEMINI_MODELS, "groq": GROQ_MODELS, "openai": OPENAI_MODELS}
    if args.model not in {entry.id for entry in menu[args.provider]}:
        raise ValueError("Choose an existing visible model for this provider")
    report = {"mode": "live_text" if args.execute_provider else "dry_plan", "production_approved": False,
        "provider": args.provider, "model": args.model, "case_ids": [r["case"]["id"] for r in selected],
        "configured_profile": {"temperature": args.temperature, "visible_token_target": args.max_tokens},
        "candidate": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "limits": {"requests": args.max_requests, "reserved_output_tokens": args.token_budget,
                   "request_bytes": 262144, "total_request_bytes": 16777216, "case_seconds": 35, "run_seconds": 1200},
        "input_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in FIXTURES.glob("ag02*.json")},
        "scope": "Actual traditional TurnStreamer and tool adapters; synthetic knowledge. No audio, contacts, database, connectors or human approval.",
        "cases": [], "error": None}
    source_paths = subprocess.check_output(["git", "ls-files", "backend/app", "backend/requirements.txt"],
                                          cwd=ROOT, text=True).splitlines()
    source_paths += [str(Path(__file__).relative_to(ROOT))]
    report["source_sha256_lf"] = {name: hashlib.sha256((ROOT / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        for name in sorted(set(source_paths)) if (ROOT / name).is_file()}
    report["environment"] = {"python": sys.version, "packages": {}}
    for package in ("httpx", "groq", "google-genai", "pydantic"):
        try:
            report["environment"]["packages"][package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            report["environment"]["packages"][package] = None
    transport = None
    provider = DryProvider(args.provider, args.model, args.max_tokens)
    try:
        if args.execute_provider:
            if not args.credential_env or not os.environ.get(args.credential_env):
                raise ValueError("Designated credential environment variable is required")
            transport = BoundedTransport(args.provider, args.model, max_requests=args.max_requests, token_budget=args.token_budget)
            provider = await provider_for(args, transport, os.environ[args.credential_env])
        async with asyncio.timeout(1200):
            for row in selected:
                report["cases"].append(await run_case(row, args, provider))
    except Exception as exc:
        # Provider exceptions can contain credential-bearing URLs; preserve type only.
        report["error"] = type(exc).__name__
    finally:
        if transport is not None:
            report["requests"] = transport.records
            report["reserved_output_tokens"] = transport.reserved_tokens
            report["budget_refusals"] = transport.refusals
            report["expected_effective_profile"] = transport.expected_profile
            if transport.refusals or any(r["response_status"] != 200 for r in transport.records):
                report["error"] = report["error"] or "IncompleteProviderRun"
            try:
                if args.provider == "gemini" and getattr(provider, "_client", None) is not None:
                    await provider._client.aio.aclose()
                    provider._client.close()
                if hasattr(provider, "cleanup"):
                    await provider.cleanup()
            except Exception as exc:
                report["cleanup_error"] = type(exc).__name__
            finally:
                await transport.aclose()
        else:
            report["prepared_requests"] = provider.prepared
        report["source_unchanged"] = all(
            hashlib.sha256((ROOT / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest() == digest
            for name, digest in report["source_sha256_lf"].items())
        if not report["source_unchanged"]:
            report["error"] = report["error"] or "SourceChangedDuringRun"
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=ENDPOINTS, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--temperature", type=float, default=0.4)
    parser.add_argument("--max-tokens", type=int, default=256)
    parser.add_argument("--max-requests", type=int, default=160)
    parser.add_argument("--token-budget", type=int, default=204800)
    parser.add_argument("--case", action="append")
    parser.add_argument("--credential-env")
    parser.add_argument("--execute-provider", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    report = asyncio.run(run(parser.parse_args()))
    print(json.dumps({"mode": report["mode"], "cases": len(report["cases"]), "error": report["error"], "human_approval": None}))
    return 1 if report["error"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
