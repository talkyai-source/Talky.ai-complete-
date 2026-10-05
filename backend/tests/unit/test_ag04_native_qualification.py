"""Behavioral checks for offline qualification evidence; no quality self-score."""
import importlib.util
import json
import socket
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("ag04_native", ROOT / "backend/tests/qualification/ag04_native.py")
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)


@pytest.mark.asyncio
async def test_declared_corpus_runs_actual_native_boundaries_without_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Offline qualification attempted network access")

    # Windows asyncio makes a loopback socketpair while creating/tearing down
    # a loop. Scope the ban to the running harness, not pytest's loop fixture.
    with monkeypatch.context() as network:
        network.setattr(socket.socket, "connect", forbidden)
        network.setattr(socket.socket, "connect_ex", forbidden)
        network.setattr(socket, "getaddrinfo", forbidden)
        rows = await native.run_cases(ROOT)
    inventory = native.case_inventory(ROOT)
    keys = lambda items: {(r["scenario_id"], r["profile"]["provider"]) for r in items}
    assert keys(rows) == keys(inventory)
    assert len(rows) == len(inventory) == 66  # 33 controls, two parser implementations.
    failed = [(row["scenario_id"], row["profile"]["provider"], finding)
        for row in rows for finding in row["findings"]["control"] if finding["pass"] is not True]
    assert not failed, json.dumps(failed, indent=2)
    assert all(row["findings"]["control"] for row in rows)
    assert all(finding["status"] == "unreviewed" for row in rows for finding in row["findings"]["semantic"])
    blocked = next(row for row in rows if row["scenario_id"] == "native.customer_denial" and row["profile"]["provider"] == "openai")
    assert blocked["raw_output"][0]["text"] == "Our records show you are an existing customer."
    assert blocked["submitted_speech"] == [] and blocked["media"]["submissions"] == []
    partial = next(row for row in rows if row["scenario_id"] == "native.late_asr_during_playout" and row["profile"]["provider"] == "openai")
    assert len(partial["media"]["submissions"]) == 1 and partial["submitted_speech"] == []
    assert partial["media"]["truncate_events"]
    assert any((row.get("metadata", {}).get("delivery") or {}).get("status") == "interrupted" for row in partial["history"])
    assert partial["media"]["receipt_origin"] == "synthetic gateway fixture"
    for provider in ("openai", "xai"):
        provider_rows = {row["scenario_id"]: row for row in rows if row["profile"]["provider"] == provider}
        for case_id in ("native.dnc_close", "native.dnc_question"):
            success = provider_rows[case_id]
            assert success["effects"]["dnc_persistence_receipts"] == [{
                "acknowledged": True, "origin": "synthetic persistence port", "database_writes": 0}]
            assert success["submitted_speech"]
        for case_id, acknowledgement in (("native.dnc_failed_receipt", False), ("native.dnc_unknown_receipt", None)):
            withheld = provider_rows[case_id]
            assert withheld["raw_output"]  # Keep the unsafe scripted candidate visible for review.
            assert withheld["submitted_speech"] == [] and withheld["media"]["submissions"] == []
            assert not any(turn["role"] == "assistant" for turn in withheld["history"])
            assert withheld["effects"]["dnc_persistence_receipts"] == [{
                "acknowledged": acknowledgement, "origin": "synthetic persistence port", "database_writes": 0}]
            assert withheld["end"]["shutdown_count"] == 0
            repairs = withheld["requests"][0]["repair_requests"]
            assert len(repairs) == 1
            repair = repairs[0]["response"]
            assert "do-not-call write is not acknowledged" in repair["instructions"]
            if provider == "openai":
                assert repair["metadata"]["talky_dnc_repair_id"]
            else:
                assert "metadata" not in repair
        assert provider_rows["native.quoted_dnc"]["effects"]["dnc_persistence_receipts"] == []
    assert all(row["end"]["dnc_effect_count"] is None and row["effects"]["dnc_database_writes"] == 0 for row in rows)


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
        LiveConversationState, CustomerRelationship, DecisionMakerStatus,
        InterestLevel, PainPriority, RequestedNextAction, SalesStage,
        MAX_LIVE_STATE_BLOCK_CHARS, render_live_state_block, replace_live_state_block,
    )
    state = LiveConversationState(identity_introduced=identity,
        decision_maker=DecisionMakerStatus.UNKNOWN, current_provider="A" * 48,
        pain_priority=PainPriority.RELIABILITY, interest_level=InterestLevel.UNKNOWN,
        refusal_count=1000, requested_next_action=RequestedNextAction.MORE_INFORMATION,
        confirmed_email="a" * 115 + "@example.test", confirmed_phone="1" * 32,
        last_tool_name="a" * 48, last_tool_success=False, last_tool_code="x" * 32,
        sales_stage=SalesStage.QUALIFICATION, customer_relationship=CustomerRelationship.AFFIRMED)
    block = render_live_state_block(state, opening_interrupted=True)
    assert len(block) <= MAX_LIVE_STATE_BLOCK_CHARS == 768
    assert "opening=interrupted" in replace_live_state_block("BASE", block)
    for field in (state.confirmed_email, state.current_provider, state.last_tool_code,
                  "customer_relationship=affirmed", "confirmed_contacts=", "sales_stage=qualification"):
        assert field in block


@pytest.mark.parametrize("prefix", ["Correction,", "Actually,", "Correction:", "Actually"])
@pytest.mark.parametrize("was_confirmed", [False, True])
def test_explicit_self_email_correction_remains_pending_until_new_confirmation(prefix, was_confirmed):
    from app.services.scripts.call_state_tracker import CallState, update_state_from_user_turn

    previous = CallState(email="alex@example.com", email_confirmed=was_confirmed)
    updated = update_state_from_user_turn(previous, f"{prefix} my email is blair at example dot com.")
    assert updated.email == "blair@example.com"
    assert updated.email_confirmed is False
    assert updated.email_capture.status.value == "awaiting_confirmation"
    assert previous.email == "alex@example.com"


@pytest.mark.parametrize("text", [
    '"Correction, my email is blair at example dot com."',
    'My colleague said "Actually, my email is blair at example dot com."',
    "Correction, my colleague's email is blair at example dot com.",
    "Actually, my email is not blair at example dot com.",
    "Correction, my email might be blair at example dot com.",
    "Correction, my email is blair or claire at example dot com.",
])
def test_correction_prefix_does_not_infer_quoted_third_party_negated_or_unclear_email(text):
    from app.services.scripts.call_state_tracker import CallState, update_state_from_user_turn

    updated = update_state_from_user_turn(CallState(email="alex@example.com", email_confirmed=True), text)
    assert updated.email == "alex@example.com"
    assert updated.email_confirmed is True


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "xai"])
@pytest.mark.parametrize("receipt", ["completed", "stale", "unknown", "transmitted", "interrupted"])
async def test_exact_contact_confirmation_owns_source_affirmation_and_playback_receipt(provider, receipt):
    import hashlib

    corpus = native._corpus(ROOT)
    case = next(c for c in corpus["scenarios"] if c["id"] == "native.contact_confirmation_" + receipt)
    row = await native.NativeReplay(case, provider, corpus).run()
    assert all(c["pass"] for c in row["findings"]["control"]), row["findings"]["control"]
    contact = row["contacts"]["email"]
    source_text = case["steps"][0]["text"]
    assert contact["value_source"] == {"provider_item_id": "contact-source", "caller_turn_order": 1,
        "revision_sha256": hashlib.sha256(source_text.encode()).hexdigest()}
    assert contact["value"] == "alex@example.com"
    assert row["raw_output"][0]["text"] == "Your email is alex@example.com, correct?"
    if receipt == "completed":
        assert contact["confirmed"] is True and contact["capture_status"] == "confirmed"
        assert contact["confirmation_source"] == {"provider_item_id": "contact-confirmation", "caller_turn_order": 2,
            "revision_sha256": hashlib.sha256(b"Yes.").hexdigest()}
        assert contact["confirmation_evidence"] == "readback_and_caller_affirmation"
        receipt_row = row["media"]["receipts"][0]
        assert contact["readback"] == {k: receipt_row[k] for k in ("utterance_id", "status", "evidence")}
        assert contact["readback"]["utterance_id"] == row["media"]["submissions"][0]["utterance_id"]
    else:
        assert contact["confirmed"] is False and contact["capture_status"] == "awaiting_confirmation"
        assert contact["confirmation_source"] is None and contact["readback"] is None
        assert row["submitted_speech"] == []
        if receipt == "interrupted":
            assert row["media"]["receipts"] == []
            assert row["media"]["truncate_events"]
            assert any((turn.get("metadata", {}).get("delivery") or {}).get("status") == "interrupted"
                for turn in row["history"] if turn["role"] == "assistant")
        elif receipt == "transmitted":
            assert row["media"]["receipts"][0]["evidence"] == "transmitted"
        elif receipt == "stale":
            assert row["media"]["receipts"][0]["utterance_id"] != row["media"]["submissions"][0]["utterance_id"]
    assert row["provenance"]["provider_calls"] == 0
    assert all(f["status"] == "unreviewed" for f in row["findings"]["semantic"])


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
