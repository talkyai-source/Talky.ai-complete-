"""Opt-in, synthetic-only qualification of the existing Groq knowledge tool turn.

Default: write a dry plan, make no provider request, and read no credentials.
Mechanical source observations never award original-question answer fidelity.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
from contextlib import aclosing
from dataclasses import dataclass, asdict
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import httpx

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
GOLD = BACKEND / "tests/fixtures/knowledge/ag02_gold.json"
HOLDOUTS = BACKEND / "tests/fixtures/knowledge/ag02_semantic_holdouts.json"
ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"
KEY_ENV = "AG02_GROQ_API_KEY"
MAX_RESPONSE_BYTES = 131072


class QualificationBoundaryError(RuntimeError):
    pass


@dataclass(frozen=True)
class Profile:
    model: str
    knowledge_mode: str
    endpoint: str
    temperature: float = 0.4
    max_tokens: int = 256
    max_requests: int = 160
    max_completion_per_request: int = 1280
    max_total_completion_tokens: int = 204800
    max_input_bytes: int = 65536
    max_total_input_bytes: int = 10485760
    case_timeout_seconds: int = 30
    total_timeout_seconds: int = 2400


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def forbid_dotenv_reads():
    """Fail closed even if a future imported module begins loading .env files."""
    def audit(event, args):
        if event == "open" and args and isinstance(args[0], (str, bytes, os.PathLike)):
            name = Path(os.fsdecode(args[0])).name.casefold()
            if name == ".env" or name.startswith(".env."):
                raise QualificationBoundaryError("Environment-file access is not permitted")
    sys.addaudithook(audit)


def runtime():
    from app.domain.models.ai_config import GROQ_MODELS
    from app.domain.models.conversation import Message, MessageRole
    from app.domain.services.voice_pipeline.knowledge_tool import (
        KB_TOOL_NAME, KNOWLEDGE_TOOL_SPEC, run_knowledge_lookup, tool_system_addendum,
    )
    from app.infrastructure.llm.groq import GroqLLMProvider, _THINKING_RESERVE_TOKENS
    return SimpleNamespace(models={entry.id for entry in GROQ_MODELS}, Message=Message,
        MessageRole=MessageRole, tool_name=KB_TOOL_NAME, tool_spec=KNOWLEDGE_TOOL_SPEC,
        lookup=run_knowledge_lookup, addendum=tool_system_addendum,
        Provider=GroqLLMProvider, reserve=_THINKING_RESERVE_TOKENS)


def validate_profile(profile: Profile, rt):
    if profile.endpoint != ENDPOINT or profile.model not in rt.models:
        raise QualificationBoundaryError("Explicit existing visible Groq model and exact endpoint required")
    if profile.knowledge_mode != "retrieve":
        raise QualificationBoundaryError("This qualification is restricted to the existing retrieve profile")
    if profile.temperature != 0.4 or profile.max_tokens != 256:
        raise QualificationBoundaryError("This predeclared profile uses temperature0.4 and configured256 tokens")
    if rt.reserve != 1024:
        raise QualificationBoundaryError("Unexpected reasoning reserve; re-review the dry plan")
    ceilings = {"max_requests": 160, "max_completion_per_request": 1280,
                "max_total_completion_tokens": 204800, "max_input_bytes": 65536,
                "max_total_input_bytes": 10485760, "case_timeout_seconds": 30,
                "total_timeout_seconds": 2400}
    for key, ceiling in ceilings.items():
        value = getattr(profile, key)
        if type(value) is not int or not 1 <= value <= ceiling:
            raise QualificationBoundaryError(f"Invalid bounded limit: {key}")


def load_cases():
    gold = json.loads(GOLD.read_text(encoding="utf-8"))
    holdouts = json.loads(HOLDOUTS.read_text(encoding="utf-8"))
    assert len(gold["cases"]) == 60 and sum(c["answerable"] for c in gold["cases"]) == 45
    assert len(holdouts["cases"]) == 20
    cases = [{**copy.deepcopy(case), "suite": "gold", "nodes": copy.deepcopy(gold["nodes"])}
             for case in gold["cases"]]
    for case in holdouts["cases"]:
        nodes = [{**copy.deepcopy(n), "source_id": "synthetic-" + case["corpus"],
                  "source_version": 1, "version": 1, "depth": 1, "summary": "", "priority": 0}
                 for n in holdouts["corpora"][case["corpus"]]]
        cases.append({**copy.deepcopy(case), "suite": "holdout", "nodes": nodes})
    return gold, cases


def model_inputs(case, profile, rt):
    # No evaluator labels, IDs, fragments, rubrics or per-case expected headings.
    prompt = rt.addendum()
    messages = []
    if case.get("previous_query"):
        messages.append(rt.Message(role=rt.MessageRole.USER, content=case["previous_query"]))
    messages.append(rt.Message(role=rt.MessageRole.USER, content=case["query"]))
    assert messages[-1].content == case["query"]
    return prompt, messages


def source_hashes():
    names = ["scripts/qualify_ag02_semantic.py", "scripts/evaluate_ag02_knowledge.py",
             "tests/fixtures/knowledge/ag02_gold.json", "tests/fixtures/knowledge/ag02_semantic_holdouts.json",
             "app/infrastructure/llm/groq.py", "app/infrastructure/llm/streaming.py",
             "app/infrastructure/llm/request_profile.py", "app/domain/models/ai_config.py",
             "app/domain/services/voice_pipeline/knowledge_tool.py",
             "app/domain/services/voice_pipeline/kb_budget.py",
             "app/services/scripts/knowledge/retrieval.py", "app/services/scripts/knowledge/passages.py",
             "app/services/scripts/knowledge/session_inject.py"]
    return {name: digest((BACKEND / name).read_bytes()) for name in names}


def dry_plan(profile, rt):
    validate_profile(profile, rt)
    gold, cases = load_cases()
    return {"status": "dry_run_no_provider_requests", "profile": asdict(profile),
            "runtime": {"python": sys.version, "executable": sys.executable,
                        "groq": importlib.metadata.version("groq"), "httpx": importlib.metadata.version("httpx"),
                        "qualification": "Existing environment; no exact-requirements claim"},
            "case_count": len(cases), "gold_count": 60, "holdout_count": 20,
            "configured_visible_target": profile.max_tokens,
            "effective_completion_ceiling": profile.max_tokens + rt.reserve,
            "credential_source": f"Explicit process environment variable {KEY_ENV}; never .env",
            "model_inputs": "Original question/prior caller context and current app tool addendum/spec; no knowledge map",
            "excluded_from_model_input": ["expected source IDs", "required fragments", "human rubrics", "answerability labels"],
            "tools": [rt.tool_name], "source_hashes": source_hashes(),
            "targets_predeclared": gold["targets_predeclared_before_first_matrix_run"],
            "human_answer_fidelity": "pending", "semantic_quality_pass": None,
            "limits": ["Actual HTTP retries consume request/completion/input budgets; exhausted runs remain incomplete.",
                       "Completion ceilings reserve reasoning plus visible tokens, not actual usage or exact spend.",
                       "Input is byte-bounded; no exact input-token or latency guarantee.",
                       "No DB, telephony, action execution, warmup, model listing or alternate provider.",
                       "Exercises the existing tool subsystem/addendum, not the complete voice persona/prompt or native audio path.",
                       "Matched rewritten query is not proof of original-question entailment."]}


class CapturedStream(httpx.AsyncByteStream):
    def __init__(self, inner, record):
        self.inner, self.record, self.body = inner, record, bytearray()
        self.iterator = None

    def __aiter__(self):
        self.iterator = self._iterate()
        return self.iterator

    async def _iterate(self):
        try:
            async for chunk in self.inner:
                if len(self.body) + len(chunk) > MAX_RESPONSE_BYTES:
                    raise QualificationBoundaryError("Response capture limit exceeded")
                self.body.extend(chunk)
                yield chunk
        finally:
            self.record["response_sse"] = self.body.decode("utf-8", errors="replace")
            self.record["response_bytes"] = len(self.body)
            events = []
            for line in self.record["response_sse"].splitlines():
                if line.startswith("data: ") and line != "data: [DONE]":
                    try:
                        events.append(json.loads(line[6:]))
                    except ValueError:
                        continue
            self.record["usage_events"] = [e.get("usage") or (e.get("x_groq") or {}).get("usage")
                                           for e in events if e.get("usage") or (e.get("x_groq") or {}).get("usage")]

    async def aclose(self):
        if self.iterator is not None:
            await self.iterator.aclose()
        await self.inner.aclose()


class BoundedTransport(httpx.AsyncBaseTransport):
    """Only this transport can dispatch; no headers or API keys are recorded."""
    def __init__(self, inner, profile):
        self.inner, self.profile = inner, profile
        self.requests, self.denials = [], []
        self.reserved_completion = self.input_bytes = 0
        self.case = None

    async def handle_async_request(self, request):
        p = self.profile
        body = await request.aread()
        payload = json.loads(body)
        ceiling = payload.get("max_completion_tokens")
        reason = None
        if request.method != "POST" or str(request.url) != p.endpoint:
            reason = "destination_or_method"
        elif payload.get("model") != p.model or payload.get("stream") is not True:
            reason = "model_or_stream"
        elif payload.get("temperature") != p.temperature or payload.get("reasoning_effort") != "low" or payload.get("include_reasoning") is not False:
            reason = "effective_profile"
        elif type(ceiling) is not int or not 1 <= ceiling <= p.max_completion_per_request:
            reason = "completion_per_request"
        elif ceiling != p.max_tokens + 1024:
            reason = "effective_completion_ceiling"
        elif len(self.requests) >= p.max_requests or self.reserved_completion + ceiling > p.max_total_completion_tokens:
            reason = "request_or_completion_budget"
        elif len(body) > p.max_input_bytes or self.input_bytes + len(body) > p.max_total_input_bytes:
            reason = "input_budget"
        elif self.case is None or self.case["query"] not in next(
                (m.get("content", "") for m in reversed(payload.get("messages", [])) if m.get("role") == "user"), ""):
            reason = "original_question_missing"
        offered = [t.get("function", {}).get("name") for t in payload.get("tools", [])]
        if any(name != "lookup_company_knowledge" for name in offered):
            reason = "unoffered_action_tool"
        if reason:
            self.denials.append(reason)
            raise QualificationBoundaryError(reason)
        self.reserved_completion += ceiling
        self.input_bytes += len(body)
        record = {"case_id": self.case["id"], "request_number": len(self.requests) + 1,
                  "request": payload, "request_sha256": digest(body), "input_bytes": len(body),
                  "reserved_completion_tokens": ceiling}
        self.requests.append(record)
        started = time.monotonic()
        try:
            response = await self.inner.handle_async_request(request)
            record["http_status"] = response.status_code
            record["provider_request_id"] = response.headers.get("x-request-id")
            response.stream = CapturedStream(response.stream, record)
            return response
        except Exception as exc:
            record["transport_error_type"] = type(exc).__name__
            raise
        finally:
            record["response_headers_ms"] = round((time.monotonic() - started) * 1000, 3)

    async def aclose(self):
        await self.inner.aclose()


async def build_provider(rt, profile, transport, api_key):
    from groq import AsyncGroq

    class CapturedGroq(rt.Provider):
        def _client_for(self, key):
            if key != api_key:
                raise QualificationBoundaryError("Only the designated credential may initialize a client")
            if key not in self._clients_by_key:
                client = httpx.AsyncClient(transport=transport, trust_env=False, follow_redirects=False,
                                          timeout=httpx.Timeout(10, connect=2))
                self._clients_by_key[key] = AsyncGroq(api_key=key, base_url="https://api.groq.com",
                                                    http_client=client, max_retries=0)
            return self._clients_by_key[key]

    provider = CapturedGroq()
    await provider.initialize({"api_key": api_key, "model": profile.model,
                               "temperature": profile.temperature, "max_tokens": profile.max_tokens})
    return provider


async def run_case(provider, transport, profile, rt, case):
    prompt, messages = model_inputs(case, profile, rt)
    session = SimpleNamespace(call_id="synthetic-" + case["id"], tenant_id="synthetic-only",
                              campaign_id="synthetic-only", knowledge_mode=profile.knowledge_mode,
                              _knowledge_snapshot_nodes=copy.deepcopy(case["nodes"]))
    record = {"id": case["id"], "suite": case["suite"], "original_question": case["query"],
              "previous_question": case.get("previous_query"), "lookups": [],
              "emitted_output": "", "model_final_output": "", "human_answer_fidelity": "pending", "semantic_quality_pass": None,
              "expected_node_ids_for_review_only": case.get("expected_node_ids", []),
              "human_review_rule": case.get("human_review_rule"), "completed": False}
    transport.case = case
    before = len(transport.requests)
    started = time.monotonic()

    async def lookup(name, args):
        if name != rt.tool_name or not isinstance(args.get("query"), str):
            raise QualificationBoundaryError("Only the existing read-only knowledge tool is available")
        query = args["query"]
        lookup_start = time.monotonic()
        result = await rt.lookup(session, query)
        record["lookups"].append({"query": query, "evidence": copy.deepcopy(session._knowledge_evidence),
                                  "tool_result": result, "elapsed_ms": round((time.monotonic()-lookup_start)*1000, 3)})
        return result

    try:
        async with asyncio.timeout(profile.case_timeout_seconds):
            output = []
            async with aclosing(provider.stream_chat_with_tools(messages, system_prompt=prompt,
                    tools=[rt.tool_spec], tool_runner=lookup, require_tool_result_before_content=True,
                    temperature=profile.temperature, max_tokens=profile.max_tokens)) as stream:
                async for text in stream:
                    output.append(text)
                    record["emitted_output"] = "".join(output)
            record["completed"] = True
    except Exception as exc:
        record["error_type"] = type(exc).__name__
        cause = exc.__cause__
        record["error_cause_types"] = []
        while cause is not None and len(record["error_cause_types"]) < 5:
            record["error_cause_types"].append(type(cause).__name__)
            cause = cause.__cause__
    finally:
        record["elapsed_ms"] = round((time.monotonic() - started) * 1000, 3)
        record["request_numbers"] = [r["request_number"] for r in transport.requests[before:]]
        if transport.requests[before:]:
            last_request = transport.requests[-1]
            pieces = []
            for line in last_request.get("response_sse", "").splitlines():
                if line.startswith("data: ") and line != "data: [DONE]":
                    try:
                        event = json.loads(line[6:])
                    except ValueError:
                        continue
                    pieces.extend(choice.get("delta", {}).get("content") or "" for choice in event.get("choices", []))
            record["model_final_output"] = "".join(pieces)
        last = record["lookups"][-1]["evidence"] if record["lookups"] else {"status": "no_lookup", "passages": []}
        passages = last.get("passages", [])
        expected = set(case.get("expected_node_ids", []))
        selected = [p for p in passages if p["node_id"] in expected and
                    (case["suite"] != "gold" or p.get("source_version") == case["expected_source_version"])]
        text = "\n".join(p["text"] for p in selected).casefold()
        missing = [s for s in case.get("required_source_fragments", []) if s.casefold() not in text]
        record["mechanical"] = {"latest_lookup_status": last["status"],
            "expected_source_selected": bool(selected) if expected else None,
            "expected_fragments_present": not missing if expected else None,
            "matched_expected_passage": bool(selected) and not missing and last["status"] == "matched",
            "missing_fragments": missing,
            "meaning": "Source selection/lexical admission only; not original-question entailment or answer correctness"}
    return record


async def execute(profile, rt, plan, output, api_key):
    from scripts.evaluate_ag02_knowledge import effective_query, evaluate_case, summarize
    from app.services.scripts.knowledge.retrieval import retrieve_pinned_knowledge
    gold, cases = load_cases()
    transport = BoundedTransport(httpx.AsyncHTTPTransport(retries=0, trust_env=False), profile)
    provider = await build_provider(rt, profile, transport, api_key)
    report = {**plan, "status": "provider_run_incomplete", "cases": [],
              "requests": transport.requests, "boundary_denials": transport.denials,
              "lexical_raw_gate": summarize(gold, [evaluate_case(c, retrieve_pinned_knowledge(
                  gold["nodes"], effective_query(c), k=3)) for c in gold["cases"]], path="unchanged raw pinned retrieval"),
              "provider_answer_fidelity": "pending human review of every answer; not automatically awarded"}
    started = time.monotonic()
    try:
        try:
            async with asyncio.timeout(profile.total_timeout_seconds):
                for case in cases:
                    if transport.denials:
                        break
                    result = await run_case(provider, transport, profile, rt, case)
                    report["cases"].append(result)
                    output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        except TimeoutError:
            report["total_wall_timeout"] = True
        report["status"] = "provider_run_complete_human_review_pending" if len(report["cases"]) == len(cases) and all(
            r["completed"] for r in report["cases"]) else "provider_run_incomplete"
    finally:
        await provider.cleanup()
        report["reserved_completion_tokens"] = transport.reserved_completion
        report["request_input_bytes"] = transport.input_bytes
        report["actual_http_requests"] = len(transport.requests)
        report["elapsed_ms"] = round((time.monotonic() - started) * 1000, 3)
        report["unrun_case_ids"] = [c["id"] for c in cases[len(report["cases"]):]]
        report["source_hashes_after"] = source_hashes()
        report["source_unchanged"] = report["source_hashes"] == report["source_hashes_after"]
        output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0 if report["status"] == "provider_run_complete_human_review_pending" and report["source_unchanged"] else 2


def main(argv=None):
    # This historical profile qualified query reformulation. Live retrieval now
    # uses model-selected section IDs, so its scores/budgets cannot qualify the
    # replacement. Preserve fixtures and evidence; do not run a misleading test.
    raise QualificationBoundaryError(
        "The query-based qualification profile is superseded by model-selected "
        "section retrieval. A reviewed section-selection profile is required."
    )


def _legacy_main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--knowledge-mode", choices=["retrieve"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute-provider", action="store_true")
    parser.add_argument("--max-requests", type=int, default=160)
    parser.add_argument("--max-total-completion-tokens", type=int, default=204800)
    args = parser.parse_args(argv)
    forbid_dotenv_reads()
    rt = runtime()
    profile = Profile(model=args.model, endpoint=args.endpoint, knowledge_mode=args.knowledge_mode,
                      max_requests=args.max_requests, max_total_completion_tokens=args.max_total_completion_tokens)
    plan = dry_plan(profile, rt)
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as file:
        file.write(json.dumps(plan, indent=2) + "\n")
    if not args.execute_provider:
        return 0
    api_key = os.environ.get(KEY_ENV)
    if not api_key or not api_key.strip():
        raise QualificationBoundaryError(f"Supply only the designated account key through {KEY_ENV}")
    return asyncio.run(execute(profile, rt, plan, output, api_key))


if __name__ == "__main__":
    raise SystemExit(main())
