"""Behavioral checks for offline qualification evidence; no quality self-score."""
import importlib.util
import json
import socket
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("ag04_native", ROOT / "backend/tests/qualification/ag04_native.py")
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "xai"])
@pytest.mark.parametrize("variant", ["supported", "absent_link"])
async def test_nonprice_grounding_preserves_source_and_useful_answer_without_semantic_approval(monkeypatch, provider, variant):
    corpus = native._corpus(ROOT)
    case = next(c for c in corpus["scenarios"] if c["id"] == "native.nonprice_" + variant)
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Offline grounding control attempted network access")
    with monkeypatch.context() as network:
        network.setattr(socket.socket, "connect", forbidden)
        network.setattr(socket.socket, "connect_ex", forbidden)
        network.setattr(socket, "getaddrinfo", forbidden)
        row = await native.NativeReplay(case, provider, corpus).run()
    assert all(c["pass"] for c in row["findings"]["control"]), row["findings"]["control"]
    evidence = row["effects"]["tool_results"][0]
    source = case["source_facts"][0]
    assert evidence["status"] == "matched" and evidence["source_policy"] == "admission_snapshot"
    assert source["content"] in evidence["text"]
    assert [{k: v for k, v in p.items() if k != "coverage"} for p in evidence["sources"]] == [
        {"node_id": source["id"], "version": source["version"],
         "source_id": source["source_id"], "source_version": source["source_version"]}]
    assert all(0 < p["coverage"] <= 1 for p in evidence["sources"])
    assert row["submitted_speech"] == case["expected_submitted_speech"]
    assert row["effects"]["executor_attempts"] == [] and row["end"]["shutdown_count"] == 0
    assert row["semantic_ids"] == ["ag04.grounded_answer"]
    assert all(f["status"] == "unreviewed" for f in row["findings"]["semantic"])
    if variant == "absent_link":
        assert "download link" in row["raw_output"][0]["text"]
        assert row["media"]["submissions"] == []
        assert len(row["requests"][0]["repair_requests"]) == 1
        assert not any(t["role"] == "assistant" for t in row["history"])
    else:
        assert row["submitted_speech"] == [source["content"]]
        assistant = [t for t in row["history"] if t["role"] == "assistant"]
        assert [t["content"] for t in assistant] == [source["content"]]
        assert assistant[0]["metadata"]["delivery"]["status"] == "completed"
        assert assistant[0]["metadata"]["delivery"]["evidence"] == "transport_played"


@pytest.mark.asyncio
async def test_nonprice_common_controls_fail_when_source_is_missing_or_answer_is_only_abstention():
    corpus = native._corpus(ROOT)
    original = next(c for c in corpus["scenarios"] if c["id"] == "native.nonprice_supported")
    for mutation in ("missing_source", "abstention"):
        case = deepcopy(original)
        if mutation == "missing_source":
            case["source_facts"] = []
        else:
            case["steps"][-1]["text"] = "I cannot confirm that."
        row = await native.NativeReplay(case, "openai", corpus).run()
        assert any(c["pass"] is False for c in row["findings"]["control"])
        assert all(f["status"] == "unreviewed" for f in row["findings"]["semantic"])


@pytest.mark.asyncio
async def test_native_source_outside_empty_fence_and_missing_delivered_history_fail_common_controls():
    corpus = native._corpus(ROOT)
    case = next(c for c in corpus["scenarios"] if c["id"] == "native.nonprice_supported")
    replay = native.NativeReplay(case, "openai", corpus)
    row = await replay.run()
    assert all(c["pass"] for c in row["findings"]["control"])
    message = next(m for m in replay.socket.sent if m.get("item", {}).get("type") == "function_call_output")
    original_output = message["item"]["output"]
    result = json.loads(message["item"]["output"])
    result["text"] = case["source_facts"][0]["content"] + "\n<company_knowledge></company_knowledge>"
    message["item"]["output"] = json.dumps(result)
    corrupted = replay.result()
    check = next(c for c in corrupted["findings"]["control"] if c["id"] == "knowledge_source_fence:0")
    assert check["pass"] is False
    # Preserve genuine source evidence but remove the assistant-history projection.
    message["item"]["output"] = original_output
    replay.transcripts.get_transcript_json = lambda _call_id: [t for t in row["history"] if t["role"] != "assistant"]
    corrupted = replay.result()
    check = next(c for c in corrupted["findings"]["control"] if c["id"] == "useful_assistant_history")
    assert check["pass"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "xai"])
async def test_missing_dnc_fixture_acknowledgement_does_not_become_success(provider):
    corpus = native._corpus(ROOT)
    case = dict(next(case for case in corpus["scenarios"] if case["id"] == "native.dnc_close"))
    case.pop("dnc_acknowledgement")
    row = await native.NativeReplay(case, provider, corpus).run()
    assert any(finding["pass"] is False for finding in row["findings"]["control"])
    assert row["observed"]["dnc_acknowledged"] is False
    assert row["submitted_speech"] == [] and row["media"]["submissions"] == []
    assert row["end"]["shutdown_count"] == 0
    assert row["effects"]["dnc_persistence_receipts"][0]["acknowledged"] is None
    assert all(finding["status"] == "unreviewed" for finding in row["findings"]["semantic"])


@pytest.mark.asyncio
async def test_observed_runtime_failure_is_reported_not_rewritten_as_approval():
    corpus = native._corpus(ROOT)
    case = dict(next(case for case in corpus["scenarios"] if case["id"] == "native.customer_denial"))
    # Remove the correction from the supplied wire sequence: the native guard
    # has no evidence. The harness must preserve this control failure.
    case["steps"] = case["steps"][1:]
    row = await native.NativeReplay(case, "openai", corpus).run()
    assert any(finding["pass"] is False for finding in row["findings"]["control"])
    assert row["media"]["submissions"]
    assert row["submitted_speech"] == ["Our records show you are an existing customer."]
    assert all(finding["status"] == "unreviewed" for finding in row["findings"]["semantic"])


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "xai"])
async def test_interrupted_opening_reaches_actual_provider_instruction_serializer(provider):
    corpus = native._corpus(ROOT)
    case = next(case for case in corpus["scenarios"] if case["id"] == "native.interrupted_opening")
    row = await native.NativeReplay(case, provider, corpus).run()
    assert row["observed"]["opening_interrupted"] is True
    assert row["observed"]["identity_introduced"] is None
    assert row["observed"]["opening_state_on_wire"] is True
    from app.domain.services.voice_pipeline.live_structured_state import LIVE_STATE_BLOCK_END
    for update in row["requests"][0]["instruction_updates"]:
        if "opening=interrupted" in update["instructions"]:
            assert update["instructions"].index("opening=interrupted") < update["instructions"].index(LIVE_STATE_BLOCK_END)


@pytest.mark.parametrize("identity", [None, False, True])
def test_interrupted_opening_fact_fits_populated_state_without_truncating_other_facts(identity):
    from app.domain.services.voice_pipeline.live_structured_state import (
        LiveConversationState, MAX_LIVE_STATE_BLOCK_CHARS,
        render_live_state_block, replace_live_state_block,
    )
    state = LiveConversationState(identity_introduced=identity,
        confirmed_email="a" * 115 + "@example.test", confirmed_phone="1" * 32,
        last_tool_name="a" * 48, last_tool_success=False, last_tool_code="x" * 32)
    block = render_live_state_block(state, opening_interrupted=True)
    assert len(block) <= MAX_LIVE_STATE_BLOCK_CHARS == 768
    assert "opening=interrupted" in replace_live_state_block("BASE", block)
    for field in (state.confirmed_email, state.confirmed_phone, state.last_tool_code,
                  "confirmed_contacts=", "last_tool_result="):
        assert field in block




@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "xai"])
async def test_historical_dnc_recollection_has_no_current_optout_or_close_authority(provider):
    corpus = native._corpus(ROOT)
    case = next(c for c in corpus["scenarios"] if c["id"] == "native.historical_dnc_recollection")
    row = await native.NativeReplay(case, provider, corpus).run()
    assert all(c["pass"] for c in row["findings"]["control"]), row["findings"]["control"]
    assert row["effects"]["dnc_persistence_receipts"] == []
    assert row["end"]["dnc_flag"] is False and row["end"]["shutdown_count"] == 0
    assert row["effects"]["tool_results"][0]["status"] == "caller_intent_unconfirmed"
    assert row["submitted_speech"] == ["What would you like to know about our opening hours?"]
